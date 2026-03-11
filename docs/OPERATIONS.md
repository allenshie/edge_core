## 部署與操作

### Logging 與錯誤處理

- Ingestion 任務在初始化/斷線/EOF 時會留下詳細 log。
- RTSP 重新連線等待秒數由 `EDGE_RTSP_RECONNECT` 控制。
- `PipelineScheduler` 每圈會輸出實際耗時與 sleep 秒數，可搭配 `EDGE_LOG_LEVEL=DEBUG` 觀察節奏。
- 若來源無法恢復，任務會拋 `TaskError`，WorkflowRunner 依 `EDGE_RETRY_BACKOFF` 重試並透過 Monitoring 回報。

### 同時啟動多個 Edge 實例

專案自己的啟動腳本可遍歷多份 `.env.camXX` 或其他實例設定檔，依序啟動多個 edge 節點：

```bash
cp .env.example .env.cam01
cp .env.example .env.cam02
# 調整各檔案內容...

./scripts/run_all.sh
./scripts/run_all.sh '.env.ca?'  # (可選) 使用自訂樣式
```

### Docker Compose 部署

```bash
cp .env.example .env.cam01
cp .env.example .env.cam02
# 調整 .env.camXX（MONITOR_ENDPOINT/INTEGRATION_API_BASE/RTSP URL 等）

set -a; source .env.cam01; set +a

docker compose up --build              # 只啟動 cam01
docker compose --profile cam02 up -d   # 同時啟動 cam02 profile
```

> 映像會在 `/svc/edge` 內執行 `python main.py`。請確認 `.env` 內的 `MONITOR_ENDPOINT`、`INTEGRATION_API_BASE`、`EDGE_RTSP_URL`/`EDGE_FILE_PATH` 指向容器可讀/可連線的來源。

> 共用網路：`docker-compose.yml` 預設使用外部 network `smartware_net`。未建立時請先 `docker network create smartware_net`。

> GPU 注意事項：`docker-compose.yml` 已設定 GPU 裝置需求；請確認主機已安裝 NVIDIA Container Toolkit，並視環境調整 runtime/deploy 設定。
