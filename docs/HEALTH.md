# 健康檢查與 Kubernetes Probe

`edge_core` 的 health server 為選配功能。當 `EDGE_HEALTH_SERVER_ENABLED=1` 時，會對外提供三個 HTTP 端點，供 Kubernetes 或外部監控系統使用。

## 端點合約

| 端點 | 建議用途 | 判定重點 |
| --- | --- | --- |
| `GET /startupz` | `startupProbe` | 啟動初始化是否完成。 |
| `GET /healthz` | `livenessProbe` | control loop / scheduler 是否仍在持續運作。 |
| `GET /readyz` | `readinessProbe` | 核心 runtime 是否已就緒、可接受工作。 |

## 狀態語意

- `startupz` 用於容器啟動初期，避免初始化期間過早觸發 liveness 重啟。
- `healthz` 是 liveness，只代表 control loop / runtime 還活著。
- `readyz` 是 readiness，代表核心 runtime 已完成啟動、持續有進度且可接受工作。
- `working` 與 `non-working` 都是合法 phase；只要核心 runtime 健康，兩者都應維持 `readyz=true`。
- 可視化串流是選配能力。`streaming.enabled=false`、尚未寫出第一筆 frame、FFmpeg 背景回收/backoff 或串流寫入失敗，都不會單獨讓 Pod 變成 NotReady。
- 串流可用性仍由 streaming health snapshot、state、log 與輸出速率等資訊呈現，不作為 Kubernetes readiness gate。

## Ingestion fatal

Ingestion 的重連完全由 engine 內的 supervisor 負責，平常斷線、沒有新畫面都會自動更換 worker 並重連，不會影響 probe。

只有在 worker 無法停止（卡在解碼函式庫內，timeout 也沒有生效）時，才會留下「卡住的 worker」。Python thread 無法強制結束，所以卡住的 worker 累積到 `EDGE_INGESTION_MAX_STUCK_WORKERS` 個時，ingestion 會標記為 fatal：

1. `fetch()` 開始拋出 `TaskError`，log 出現 `CRITICAL ... ingestion fatal`，並送出一筆 monitor `error` 事件。
2. `EdgeHealthState.mark_fatal()` 之後不再更新 loop tick 與 progress。
3. `readyz` 在 `EDGE_HEALTH_READINESS_TIMEOUT_SECONDS` 後失敗；`healthz` 在 `EDGE_HEALTH_LIVENESS_TIMEOUT_SECONDS` 後失敗，再經過 liveness probe 的 `periodSeconds × failureThreshold`，Kubernetes 重啟 pod。以下方建議值計算，大約 60 秒。

沒有啟用 health server 時只會寫 log，不會自動重啟。

## 建議的 Kubernetes probes

```yaml
startupProbe:
  httpGet:
    path: /startupz
    port: 8081
  periodSeconds: 2
  failureThreshold: 30

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

## 判讀建議

- `healthz=true` 且 `readyz=false`：核心 runtime 還活著，但可能尚未完成啟動、近期沒有工作進度，或已進入 runtime backoff；請檢查 scheduler / control loop 狀態。
- `healthz=false`：優先檢查 scheduler / control loop 是否卡住，或 process 是否已經異常退出；若 log 中有 `ingestion fatal`，代表是 ingestion 卡住的 worker 達到上限（見上方「Ingestion fatal」）。
- `phase=non-working` 或串流未輸出時仍應為 Ready；若此時 `readyz=false`，原因應從核心 runtime 的 startup、progress、stopping 或 backoff 狀態排查。

## smart_health 健康回報（edge_core 0.2 起）

`EDGE_HEALTH_ENABLED=1`（預設）時，啟動時建立 `HealthMonitor("edge", <EDGE_CAMERA_ID>)`，各節點的狀態由 smart-workflow 的 `BaseTask` 與回報執行緒記錄。上方的 probe server 仍照舊運作，兩者並存；舊的健康摘要 log 在階段 E 才移除。

### 節點

| 節點 | lane | 上游 | 依賴 | 啟用條件 | stale | stuck |
|---|---|---|---|---|---|---|
| `ingest-worker` | `ingest-bg` | — | — | 一律 | 5 秒（`no_new_frame`） | 20 秒 |
| `ingest` | `main` | `ingest-worker` | — | 一律 | 推算 | 20 秒 |
| `infer` | `main` | `ingest` | — | 排程這個時段有推理 | 推算 | 10 秒 |
| `stream` | `main` | `infer` | — | 推流已啟用，而且這個時段要推流 | 推算 | 5 秒 |
| `stream-output` | `stream-bg` | `stream` | `stream_sink` | 同 `stream` | 5 秒 | 10 秒 |
| `publish` | `main` | `infer` | `messaging` | `EDGE_PUBLISH_ENABLED` | 推算 | 10 秒 |

「推算」為 `max(5, 3 × EDGE_POLL_INTERVAL)`，可用 `EDGE_HEALTH_STALE_AFTER_SECONDS` 覆寫。所有節點的啟動寬限與錯誤視窗都是 30 秒。沒有新畫面時，`ingest`、`infer`、`stream`、`publish` 記為 skipped（不算成功，持續沒有新畫面就會 stale）。metrics：`infer.detections`、`publish.capture_age_ms`、`publish.capture_age_ms_max`，以及各節點的 `duration_ms`、`rate_per_s`。

### 無法恢復時

下列情況由 smart-workflow 判斷為無法恢復，寫 CRITICAL log 後以代碼 70 結束程式，交給 systemd 或 k8s 重啟：

- 三種 CUDA 錯誤（`illegal memory access`、`unspecified launch failure`、`device-side assert triggered`）：`infer` 拋出 `FatalTaskError(cuda_unrecoverable)`。改版前這些錯誤和其他推理錯誤一樣，只會中斷這一輪並退避重試，CUDA context 損壞後每一輪都會失敗。
- 卡住的 capture worker 達到 `EDGE_INGESTION_MAX_STUCK_WORKERS`（`ingest_workers_stuck`）。
- 背景執行緒（ingestion supervisor、推流輸出執行緒）在該工作時死掉（`background_dead`），或單次讀取／寫入卡住太久（`background_stuck`；ingestion 至少 30 秒，且晚於 supervisor 換 worker 的時間）。
- 主迴圈超過 `EDGE_HEALTH_LOOP_TIMEOUT_SECONDS` 沒有跑完一輪（`main_loop_stalled`）。

觀察模式（`EDGE_HEALTH_EXIT_ON_FAILURE=0`）只寫 CRITICAL log，不結束程式。

### 暖機

啟用健康回報時，啟動任務會用空白畫面把排程中所有時段的每個模型各推理一次（大小取模型設定的 `imgsz`，預設 640×640），每個模型一行 INFO 加上總耗時。YOLO 模型用 `predict` 暖機，參數和正式推理相同，不經過 tracker，避免改變第一張真實畫面的追蹤結果。暖機失敗時啟動失敗、程式結束。

### StatusServer

設定 `EDGE_HEALTH_STATUS_PORT` 時，`GET http://<host>:<port>/status` 回傳各節點的 `state`、`reason` 與 metrics。只開放內網。

## 相關設定

健康檢查的環境變數與預設值，請參考 [ENV.md](ENV.md#健康檢查k8s-probe)。
