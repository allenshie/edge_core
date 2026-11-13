# 健康檢查與健康回報

`edge_core` 對外提供兩種健康資訊：給 kubelet 的 **k8s probe**（`/startupz`、`/healthz`、`/readyz`，回 200／503），以及給 Health App 與維運人員的 **smart_health 回報**（`/status`、`/topology`，各節點的燈號、原因與 metrics）。兩者各自設定、可以任意組合：

| | k8s probe | smart_health 回報 |
| --- | --- | --- |
| 提供者 | smart-workflow `HealthServer` | smart-health-record `StatusServer` |
| 開關 | `EDGE_HEALTH_SERVER_ENABLED=1`（預設關閉） | `EDGE_HEALTH_ENABLED=1`（預設開啟），設定 `EDGE_HEALTH_STATUS_PORT` 才啟動 StatusServer |
| 場域 | k8s、docker | Orin、k8s |

兩者的關係在 `/healthz`：`EDGE_HEALTH_ENABLED=1` 且不是觀察模式時，`/healthz` 依回報執行緒的「無法恢復」判斷（已啟動、未停止、判斷正常，而且判斷在 5 秒內更新過）；觀察模式或 `EDGE_HEALTH_ENABLED=0` 時，依主迴圈心跳與 `EDGE_HEALTH_LIVENESS_TIMEOUT_SECONDS`。完整的對照表與環境變數見 [ENV.md 的「健康」](ENV.md#健康)。

## 端點合約

| 端點 | 建議用途 | 判定重點 |
| --- | --- | --- |
| `GET /startupz` | `startupProbe` | 啟動初始化是否完成。 |
| `GET /healthz` | `livenessProbe` | 程式是否仍能正常運作：啟用健康回報時看「無法恢復」判斷，否則看 control loop 心跳（見上方）。 |
| `GET /readyz` | `readinessProbe` | 核心 runtime 是否已就緒、可接受工作。 |

## 狀態語意

- `startupz` 用於容器啟動初期，避免初始化期間過早觸發 liveness 重啟。
- `healthz` 是 liveness，只代表 control loop / runtime 還活著。
- `readyz` 是 readiness，代表核心 runtime 已完成啟動、持續有進度且可接受工作。
- `working` 與 `non-working` 都是合法 phase；只要核心 runtime 健康，兩者都應維持 `readyz=true`。
- 可視化串流是選配能力。`streaming.enabled=false`、尚未寫出第一筆 frame、FFmpeg 背景回收/backoff 或串流寫入失敗，都不會單獨讓 Pod 變成 NotReady。
- 串流可用性由 `/status` 的 `stream`、`stream-output` 節點與 log 呈現，不作為 Kubernetes readiness gate。

## k8s 上的重啟方式

k8s 由程式以代碼 70 結束觸發重啟（見下方「無法恢復時」）。`/healthz` 的 503 只在結束前存在幾毫秒，不是重啟的主要途徑；livenessProbe 處理的是程式沒有結束、但已經無法回應或判斷停止更新的情況，例如整個程序凍結。

## Ingestion fatal

Ingestion 的重連完全由 engine 內的 supervisor 負責，平常斷線、沒有新畫面都會自動更換 worker 並重連，不會影響 probe。

只有在 worker 無法停止（卡在解碼函式庫內，timeout 也沒有生效）時，才會留下「卡住的 worker」。Python thread 無法強制結束，所以卡住的 worker 累積到 `EDGE_INGESTION_MAX_STUCK_WORKERS` 個時，ingestion 會標記為 fatal：log 出現 `CRITICAL ... ingestion fatal`，`fetch()` 開始拋出 `TaskError`，`ingest-worker` 節點記錄 fatal（`ingest_workers_stuck`）。之後依模式不同：

| 模式 | 結果 |
|---|---|
| 正常（`EDGE_HEALTH_ENABLED=1`、`EDGE_HEALTH_EXIT_ON_FAILURE=1`，預設） | smart-workflow 判斷為無法恢復，寫 CRITICAL log 後以代碼 70 結束程式，由 systemd 或 k8s 重啟。 |
| 觀察模式（`EDGE_HEALTH_EXIT_ON_FAILURE=0`） | 不結束程式。`/status` 的 `ingest-worker` 顯示 fatal；主迴圈照常每輪執行（每輪都失敗並退避），所以 `/healthz` 維持 200，`/readyz` 在 `EDGE_HEALTH_READINESS_TIMEOUT_SECONDS` 後變成 503。**需要手動重啟。** |
| 關閉（`EDGE_HEALTH_ENABLED=0`） | 和觀察模式相同：`/healthz` 維持 200、`/readyz` 變成 503，需要手動重啟。這個模式沒有 HealthMonitor，所以沒有 `/status`。 |

- systemd 不看 `/healthz`，ingestion fatal 後是否重啟只取決於程式是否結束。
- k8s 上不建議長期使用觀察模式或關閉模式：只有 `/readyz` 變 503，不會自動重啟。

## 建議的 Kubernetes probes

```yaml
startupProbe:
  httpGet:
    path: /startupz
    port: 8081
  periodSeconds: 2
  failureThreshold: 150

livenessProbe:
  httpGet:
    path: /healthz
    port: 8081
  periodSeconds: 10
  timeoutSeconds: 1
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /readyz
    port: 8081
  periodSeconds: 5
  timeoutSeconds: 1
  failureThreshold: 1
```

startupProbe 的總時限為 300 秒（`failureThreshold: 150` × `periodSeconds: 2`），涵蓋模型載入與暖機，和 systemd 的 `TimeoutStartSec=300` 相同。

## 判讀建議

- `healthz=true` 且 `readyz=false`：核心 runtime 還活著，但可能尚未完成啟動、近期沒有工作進度，或已進入 runtime backoff；請檢查 scheduler / control loop 狀態。
- `healthz=false`：優先檢查 scheduler / control loop 是否卡住，或 process 是否已經異常退出。
- `healthz=true`、`readyz=false`，且 log 中有 `ingestion fatal`：ingestion 卡住的 worker 達到上限，觀察模式或關閉模式下需要手動重啟（見上方「Ingestion fatal」）。
- `phase=non-working` 或串流未輸出時仍應為 Ready；若此時 `readyz=false`，原因應從核心 runtime 的 startup、progress、stopping 或 backoff 狀態排查。

## smart_health 健康回報

`EDGE_HEALTH_ENABLED=1`（預設）時，啟動時建立 `HealthMonitor("edge", <EDGE_CAMERA_ID>)`，各節點的狀態由 smart-workflow 的 `BaseTask` 與回報執行緒記錄。上方的 probe server 照常運作，兩者並存。

### 節點

| 節點 | lane | 上游 | 依賴 | 啟用條件 | stale | stuck |
|---|---|---|---|---|---|---|
| `ingest-worker` | `ingest-bg` | — | — | 一律 | 5 秒（`no_new_frame`） | 20 秒 |
| `ingest` | `main` | `ingest-worker` | — | 一律 | 推算 | 20 秒 |
| `infer` | `main` | `ingest` | — | 排程這個時段有推理 | 推算 | 10 秒 |
| `stream` | `main` | `infer` | — | 推流已啟用，而且這個時段要推流 | 推算 | 5 秒 |
| `stream-output` | `stream-bg` | `stream` | `stream_sink` | 同 `stream` | 5 秒 | 10 秒 |
| `publish` | `main` | `infer` | `messaging` | `EDGE_PUBLISH_ENABLED` | 推算 | 10 秒 |

「推算」為 `max(5, 3 × EDGE_POLL_INTERVAL, infer 的 stuck 門檻 + 1)`，預設為 11 秒（`main` 是 sequential lane，推理卡住時其他節點也沒有新的成功；門檻比 `infer` 的 10 秒長，才會由 `infer` 先轉紅），可用 `EDGE_HEALTH_STALE_AFTER_SECONDS` 覆寫。所有節點的啟動寬限與錯誤視窗都是 30 秒。沒有新畫面時，`ingest`、`infer`、`stream`、`publish` 記為 skipped（不算成功，持續沒有新畫面就會 stale）。metrics 見下方「Metrics」。

### 無法恢復時

下列情況由 smart-workflow 判斷為無法恢復，寫 CRITICAL log 後以代碼 70 結束程式，交給 systemd 或 k8s 重啟：

- 三種 CUDA 錯誤（`illegal memory access`、`unspecified launch failure`、`device-side assert triggered`）：`infer` 拋出 `FatalTaskError(cuda_unrecoverable)`。CUDA context 損壞後每一輪推理都會失敗，無法自行恢復。
- 卡住的 capture worker 達到 `EDGE_INGESTION_MAX_STUCK_WORKERS`（`ingest_workers_stuck`）。
- 背景執行緒（ingestion supervisor、推流輸出執行緒）在該工作時死掉（`background_dead`），或單次讀取／寫入卡住太久（`background_stuck`；ingestion 至少 30 秒，且晚於 supervisor 換 worker 的時間）。
- 主迴圈超過 `EDGE_HEALTH_LOOP_TIMEOUT_SECONDS` 沒有跑完一輪（`main_loop_stalled`）。

觀察模式（`EDGE_HEALTH_EXIT_ON_FAILURE=0`）只寫 CRITICAL log（每 5 秒一行），不結束程式。觀察模式下 `cuda_unrecoverable` 會一直保留到程式重啟：清除故障後 `infer` 仍是紅燈，也會遮住之後的其他判斷（真實的 CUDA 損壞本來就無法恢復）。

### 暖機

啟用健康回報時，啟動任務會用空白畫面把排程中所有時段的每個模型各推理一次（大小取模型設定的 `imgsz`，預設 640×640），每個模型一行 INFO 加上總耗時。YOLO 模型用 `predict` 暖機，參數和正式推理相同，不經過 tracker，避免改變第一張真實畫面的追蹤結果。暖機失敗時啟動失敗、程式結束。

### StatusServer

設定 `EDGE_HEALTH_STATUS_PORT` 時，`GET http://<host>:<port>/status` 回傳各節點的 `state`、`reason`、`message`、`since` 與 `metrics`。只開放內網。

```json
{"service": "edge", "instance": "cam01", "nodes": {
  "ingest": {"state": "ok", "reason": null, "metrics": {"duration_ms": 0.04, "rate_per_s": 4.0, "reconnects": 0, "read_failures": 0}},
  "publish": {"state": "ok", "reason": null, "metrics": {"published": 5, "capture_age_ms": 61.2, "capture_age_ms_max": 75.0}}
}}
```

各節點的狀態可以用 Health App 集中查看：Health App 屬於 smart_health 專案（<https://github.com/allenshie/smart-health>），部署方式見該專案的 `docs/DEPLOY.md`；Health App 的 `targets.yaml` 列出各 Edge 的 `IP:port`（即這裡的 StatusServer）。

### Metrics

在 `/status` 的 `nodes.<節點>.metrics`，定期摘要 log 也會列出。

| 節點 | metric | 內容 |
|---|---|---|
| 各節點 | `rate_per_s`、`duration_ms` | 每秒成功次數、這一輪的執行時間（背景節點只有 `rate_per_s`）。 |
| `ingest` | `reconnects` | 啟動以來 supervisor 重新連線成功的次數。 |
| `ingest` | `read_failures` | 啟動以來讀取或開啟失敗（worker 結束、要重連）的次數；取流失敗的輪次也會更新。 |
| `infer` | `detections` | 這一輪的偵測數。 |
| `publish` | `published` | 這一輪發布的筆數（沒有新畫面或停用發布的輪次不更新）。 |
| `publish` | `capture_age_ms`、`capture_age_ms_max` | 從讀到畫面到發布完成的時間，以及最近 `EDGE_HEALTH_REPORT_INTERVAL_SEC` 內的最大值。 |
| `stream` | `ffmpeg_restarts` | 啟動以來推流 ffmpeg 因寫入失敗而重新啟動的次數。 |
| `stream` | `write_failures` | 啟動以來寫入 ffmpeg 失敗的次數。 |

錯誤與異常以各節點的錯誤視窗與燈號呈現，沒有另外的累計次數。

### Log

- **狀態改變：** 由 smart_health 輸出，例如 `smart_health_record: node ingest-worker: ok -> unavailable (reason=no_new_frame)`。log 等級依新狀態決定，例如轉成 `unavailable` 為 WARNING、轉成 `degraded`／`waiting` 為 INFO。
- **定期摘要：** 每 `EDGE_HEALTH_REPORT_INTERVAL_SEC` 秒一行 INFO，例如 `健康摘要：ingest-worker=ok rate_per_s=4.0；ingest=ok rate_per_s=4.0 duration_ms=0.0 read_failures=0 reconnects=0；…`。
- **無法恢復：** `smart_workflow.health` 的 CRITICAL，例如 `判斷為無法恢復：reason=main_loop_stalled 節點=- 持續 30.5 秒，結束程式（代碼 70）`。

### 從 `/status` 與 log 判斷

| 狀況 | `/status` | log |
|---|---|---|
| 相機斷線 | `ingest-worker` 轉紅（`no_new_frame`，約 5 秒）或 `degraded`（`open_failed`），下游顯示灰色 `upstream_not_ready`；`ingest.read_failures` 持續增加，恢復後 `reconnects` 加 1 | `IngestionWorker-N 結束（…），… 秒後重新連線`、`重新連線成功` |
| 推理卡住 | `infer` 轉紅 `stuck`（10 秒），其他主迴圈節點顯示灰色 `lane_blocked`；stale 門檻 11 秒，不會比 `infer` 早轉紅 | 主迴圈超過 `EDGE_HEALTH_LOOP_TIMEOUT_SECONDS` 後 `main_loop_stalled` |
| 推流目的地斷線 | `stream-output` 在紅（`dependency_unavailable`）黃（`write_failed`）之間切換（見下方「已知的燈號現象」），`stream.ffmpeg_restarts`、`write_failures` 增加 | `streaming write failed; restart ffmpeg` |
| 訊息服務斷線 | `publish` 轉紅 `dependency_unavailable`；恢復後先黃（錯誤仍在 30 秒視窗內）再綠 | messaging client 的重連 log |
| fatal | 節點顯示 `fatal` 與原因（`cuda_unrecoverable`、`ingest_workers_stuck`） | `smart_workflow.health` 的 CRITICAL |

## 行為與實測參考

以下為本機 RTX 3080、檔案或 RTSP 測試來源的參考值，正式硬體以實測為準。

- **啟動：** 啟動到就緒約 4 秒。暖機每個模型一行，例如 `signal`、`unsignal` 兩個時段的 `detect_and_track` 各 873 ms、266 ms，總計約 1.1 秒。
- **systemd 啟動期間：** systemd 在 `READY=1` 之前不計 watchdog（systemd 245），啟動期間只受 `TimeoutStartSec` 限制。
- **相機斷線：** 約 7 秒 `ingest-worker` 轉紅。worker 以 1→2→4→8→16→30 秒退避重連，開啟失敗不會觸發 `background_stuck`。來源恢復後約 20～30 秒轉綠（退避加上等待關鍵畫面）。
- **messaging 斷線：** TCP 關閉時 1 秒內 `publish` 轉紅；broker 沒有回應但連線未關閉時，約 19 秒（`EDGE_MQTT_KEEPALIVE=15`）。恢復後先黃再綠（30 秒錯誤視窗）。
- **推理卡住：** 約 11 秒 `infer` 轉紅 `stuck`，約 30 秒 `main_loop_stalled` 結束程式。
- **整個程序凍結：** systemd watchdog（`WatchdogSec=10`）約 10 秒結束整個 control group（python 與 ffmpeg）並重啟。

## 已知的燈號現象

- **推流目的地斷線時，`stream-output` 在紅燈和黃燈之間反覆切換：** ffmpeg 重新啟動後，在發現推不出去之前會先接受幾張畫面，這些寫入算成功。可以用 `stream` 的 `ffmpeg_restarts` 持續增加來判斷。之後依實際使用的 RTMP 目的地決定是否修改。
- **推理停用的時段，`publish` 顯示灰色 `upstream_not_ready`：** smart_health 只把 `ok`、`degraded` 的上游視為就緒，雖然發布照常進行。目前的正式排程（`signal`、`unsignal`）都有推理，所以不會遇到；之後有不推理的時段時，再由 smart_health 處理。

## 相關設定

健康檢查與健康回報的環境變數與預設值，請參考 [ENV.md 的「健康」](ENV.md#健康)（[k8s probe](ENV.md#k8s-probe)、[smart_health 回報](ENV.md#smart_health-回報)）。
