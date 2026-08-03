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
- `healthz=false`：優先檢查 scheduler / control loop 是否卡住，或 process 是否已經異常退出。
- `phase=non-working` 或串流未輸出時仍應為 Ready；若此時 `readyz=false`，原因應從核心 runtime 的 startup、progress、stopping 或 backoff 狀態排查。

## 相關設定

健康檢查的環境變數與預設值，請參考 [ENV.md](ENV.md#健康檢查k8s-probe)。
