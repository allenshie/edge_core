# Edge 推理服務（edge_core）

此模組提供邊緣推理節點 runtime：取流、推理、串流輸出、事件發布。

## Pipeline

```text
RTSP / MP4
  -> IngestionTask
  -> InferenceTask
  -> StreamingTask
  -> PublishResultTask
```

- `InferenceTask` 只做推理與輸出結果。
- 可視化/串流打出由 `StreamingTask` 處理。
- `PublishResultTask` 負責推送推理事件。

## 快速啟動（獨立）

```bash
uv venv --python /usr/bin/python3.10
source .venv/bin/activate
uv pip install -e ".[vision]"
python main.py
```

## 從上層主專案啟動（建議）

```bash
# 在 smart_warehouse_edge 根目錄
uv pip install -e . -e "edge_core[vision]"
bash scripts/run_edges.sh cam01
```

## 串流策略

- 全域開關：`EDGE_STREAMING_ENABLED`
- phase 開關：`schedules/schedule.json` 的 `streaming.enabled`
- 推流 URL：`EDGE_STREAMING_URL`（例如 RTMP 到 MTX）
- 本機部署目前僅支援 CPU 編碼：`EDGE_STREAMING_STRATEGY=cpu`
- 無幀 watchdog：`EDGE_STREAMING_IDLE_TIMEOUT`
- 重啟退避：`EDGE_STREAMING_RESTART_BACKOFF`

詳見：`docs/ENV.md`

## 健康檢查（可選）

可透過環境變數啟用健康檢查 HTTP 端點：

- `EDGE_HEALTH_SERVER_ENABLED=1`
- `EDGE_HEALTH_SERVER_HOST=0.0.0.0`
- `EDGE_HEALTH_SERVER_PORT=8081`

啟用後可提供 Kubernetes probes：

- `GET /startupz`
- `GET /healthz`
- `GET /readyz`

## 參考文件

- [設定與環境變數](docs/ENV.md)
- [設定示例（多相機）](docs/CONFIG.md)
- [自訂 Inference/Publish 與 Mode 控制](docs/EXTENDING.md)
- [Orin 部署指南（ARM）](docs/DEPLOY_ORIN.md)
- [子模組導入主專案](docs/EDGE_SUBMODULE_GUIDE.md)
- [部署與操作（多實例、Docker）](docs/OPERATIONS.md)
- [測試與品質](docs/TESTING.md)
