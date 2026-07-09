## 部署與操作

### Logging 與錯誤處理

- Ingestion 任務在初始化/斷線/EOF 時會留下詳細 log。
- RTSP 重新連線等待秒數由 `EDGE_RTSP_RECONNECT` 控制。
- `PipelineScheduler` 每圈會輸出實際耗時與 sleep 秒數，可搭配 `EDGE_LOG_LEVEL=DEBUG` 觀察節奏。
- 若來源無法恢復，任務會拋 `TaskError`，WorkflowRunner 依 `EDGE_RETRY_BACKOFF` 重試並透過 Monitoring 回報。

### 同時啟動多個 Edge 實例

`edge_core/scripts/run_all.sh` 會掃描 `edge_core/env/` 底下符合樣式的 `.env` 檔，逐一在背景啟動多個 edge 節點。

常用方式：

```bash
cd edge_core
cp .env.example env/.env.cam01
cp .env.example env/.env.cam02
# 調整各檔案內容...

./scripts/run_all.sh
./scripts/run_all.sh '.env.cam0?'  # (可選) 使用自訂樣式
```

預設模式會載入 `env/.env.*`。如果你只想啟動部分實例，可以傳入 glob pattern，例如 `.env.cam0?`。

每份 `.env.camXX` 建議至少包含自己的 messaging route：

```env
EDGE_APP_INBOUND_BACKEND=mqtt
EDGE_PHASE_ENABLED=1
EDGE_PHASE_CHANNEL=integration/phase
EDGE_PHASE_RESOURCE_NAME=edge_mode
EDGE_EVENTS_BACKEND=http
EDGE_EVENTS_CHANNEL=/edge/events
```

如果該實例也要觀察 matching debug label，再另外加上：

```env
EDGE_MATCHING_RESULT_ENABLED=1
EDGE_MATCHING_RESULT_CHANNEL=integration/matching
EDGE_MATCHING_RESULT_RESOURCE_NAME=matching_result_snapshot
```

如果你是在上層 `smart_intersection_safety_edge` 專案中啟動單一或少量實例，則可以改用根目錄的 [scripts/run_edge.sh](../../scripts/run_edge.sh) 直接指定 `.env` 或 `camXX` 名稱。

### Docker Compose 部署

```bash
cp .env.example .env.cam01
cp .env.example .env.cam02
# 調整 .env.camXX（MONITOR_ENDPOINT/INTEGRATION_API_BASE/RTSP URL、EDGE_PHASE_*、EDGE_EVENTS_* 等）

set -a; source .env.cam01; set +a

docker compose up --build              # 只啟動 cam01
docker compose --profile cam02 up -d   # 同時啟動 cam02 profile
```

> 映像會在 `/svc/edge` 內執行 `python main.py`。請確認 `.env` 內的 `MONITOR_ENDPOINT`、`INTEGRATION_API_BASE`、`EDGE_RTSP_URL`/`EDGE_FILE_PATH`、`EDGE_APP_INBOUND_BACKEND`、`EDGE_PHASE_*`、`EDGE_MATCHING_RESULT_*`、`EDGE_EVENTS_*` 指向容器可讀/可連線的來源。

> 共用網路：`docker-compose.yml` 預設使用外部 network `smartware_net`。未建立時請先 `docker network create smartware_net`。

> GPU 注意事項：`docker-compose.yml` 已設定 GPU 裝置需求；請確認主機已安裝 NVIDIA Container Toolkit，並視環境調整 runtime/deploy 設定。
