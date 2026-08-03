# 健康檢查與 Kubernetes Probe

`edge_core` 的 health server 為選配功能。當 `EDGE_HEALTH_SERVER_ENABLED=1` 時，會對外提供三個 HTTP 端點，供 Kubernetes 或外部監控系統使用。

## 端點合約

| 端點 | 建議用途 | 判定重點 |
| --- | --- | --- |
| `GET /startupz` | `startupProbe` | 啟動初始化是否完成。 |
| `GET /healthz` | `livenessProbe` | control loop / scheduler 是否仍在持續運作。 |
| `GET /readyz` | `readinessProbe` | 目前是否可接受可視化串流輸出。 |

## 狀態語意

- `startupz` 用於容器啟動初期，避免初始化期間過早觸發 liveness 重啟。
- `healthz` 只代表 runtime 還活著，與 `non-working` phase 無直接關聯。
- `readyz` 代表是否已具備對外輸出能力；在 `non-working`、等待首幀、FFmpeg 背景回收、寫入失敗恢復期間，預期會是 `false`。
- 當 phase 回到 `working`，且成功寫入第一筆可視化 frame 後，`readyz` 才會恢復為 `true`。

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

- `healthz=true` 且 `readyz=false`：通常是正常的 phase 切換、等待首幀或背景回收狀態。
- `healthz=false`：優先檢查 scheduler / control loop 是否卡住，或 process 是否已經異常退出。
- `readyz=false` 且 phase 為 `non-working`：預期行為，不應直接視為故障。

## 相關設定

健康檢查的環境變數與預設值，請參考 [ENV.md](ENV.md#健康檢查k8s-probe)。
