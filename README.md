# Edge 推理服務

此模組模擬邊緣推理節點：透過 RTSP 從 NVR/Camera 取得影像 → 解碼 → 執行推理 → 將事件送至整合端，並透過 monitoring sidecar 上報狀態。架構參考既有 `client/` 專案，但以精簡版骨架實作，方便未來接上實際模型與通訊協定。

## 流程概述

```text
RTSP / MP4 → IngestionTask → InferenceTask → PublishResultTask → integration
```

- 所有 Task 皆繼承 `smart_workflow.BaseTask`（由 `git+https://github.com/allenshie/smart-workflow.git` 安裝），可共用整合端的 WorkflowRunner / TaskContext。
- Task 執行成功/失敗會透過 `TaskContext` 內的 `MonitoringClient` 呼叫 `/events` 上報；Workflow 每圈也會自動呼叫 `/heartbeat` 回報健康狀態。
- WorkflowRunner 在 `edge/src/edge/main.py` 中定義，預設使用 RTSP 模式連接 NVR/Camera 並進行推理。

## 目錄

- `src/edge/config.py`：邊緣節點設定，包含 camera ID、Ultralytics YOLO 模型路徑（`EDGE_MODEL_PATH`）、RTSP 參數與整合端 API 位置等，可從環境變數覆寫。
- `src/edge/pipeline/`：定義 workflow 與各 stage 任務。
  - `tasks/ingestion/`：提供 RTSP 與檔案兩種取流模式（`RtspIngestionTask` / `FileIngestionTask`）。
  - `tasks/inference/`：呼叫 Ultralytics YOLO 模型輸出偵測結果並繪製在影格上。
  - `tasks/publish/`：將推論資訊整理後送到整合端 API（目前以 log + Monitoring 佔位）。
- `src/edge/main.py`：WorkflowRunner 入口，負責載入設定與啟動 pipeline；根目錄 `main.py` 只是相容性啟動器。

## 快速啟動

### 依賴需求

- Python 3.10+（專案預設 3.12）
- [uv](https://github.com/astral-sh/uv) 或 `pip`/`venv` 等虛擬環境工具
- OpenCV / FFmpeg 相關系統套件（Ubuntu: `sudo apt install ffmpeg libgl1`）
- NVIDIA GPU（選用）：若需跑真實模型，請搭配 CUDA driver 與 NVIDIA Container Toolkit

在開始前可先複製 `.env.example` 作為基礎設定：

```bash
cd edge
cp .env.example .env
```

請務必依實際情境調整以下參數：

- `EDGE_INGEST_MODE`：決定使用 `file` 或 `rtsp`。
- `EDGE_FILE_PATH` 或 `EDGE_RTSP_URL`：填入可讀/可連線的影片或串流來源。
- `EDGE_MODEL_PATH`：指向可存取的權重檔。
- `MONITOR_ENDPOINT` / `INTEGRATION_API_BASE`：改成對應環境的服務位址。
若需多相機設定，可再複製到 `env/.env.camXX`。

```bash
cd edge
uv venv --python /usr/bin/python3.12  # 或 python -m venv .venv
source .venv/bin/activate
# 安裝依賴並註冊 edge 套件
uv pip install -r requirements.txt
uv pip install -e .
python main.py
```

啟動後會定期：
1. 依 `EDGE_INGEST_MODE` 連線 RTSP 或讀取 MP4 並取得最新影格。
2. 將影格交給推理與可視化邏輯，視設定輸出檔案或顯示視窗。
3. 上傳推論結果至整合端。

日誌會顯示每個 Task 的開始／結束與成功/失敗事件。若發生例外，TaskContext 會透過 MonitoringClient 的 `report_event` 回報。

## 設定

環境變數或 `.env` 可覆寫主要參數。若需管理多個攝影機設定，可複製 `edge/env/cam01.env.example` 為 `edge/env/.env.camXX` 後載入：

```bash
cd edge
cp env/cam01.env.example env/.env.cam02
set -a; source env/.env.cam02; set +a
python main.py
```

### 常用參數

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `EDGE_CAMERA_ID` | `cam01` | 邊緣節點對應的相機識別（整合端映射用）。|
| `EDGE_MODEL_NAME` | `yolo11n` | 推理模型名稱（用於 log）。|
| `EDGE_MODEL_PATH` | `yolo11n.pt` | Ultralytics YOLO 權重路徑，請先下載對應 `.pt` 放到可讀位置。|
| `EDGE_MODEL_DEVICE` | *(自動)* | 指定 `cpu`、`cuda:0` 等裝置，留空由 Ultralytics 自動判斷。|
| `EDGE_MODEL_VISUALIZE` | `1` | 是否繪製推論結果（會與下列 EDGE_VISUAL_* 一併判斷）。|
| `EDGE_VISUAL_ENABLED` | *(沿用 `EDGE_MODEL_VISUALIZE` 預設)* | 控制是否執行可視化輸出（write/show）。|
| `EDGE_VISUAL_MODE` | `write` | `write` 將輸出檔案到 `edge/output_frames/`；設定 `show` 則使用 `cv2.imshow` 顯示視窗（僅建議在開發機使用）。|
| `EDGE_VISUAL_WINDOW` | `edge-preview` | `show` 模式下的 OpenCV 視窗名稱。|
| `EDGE_VISUAL_WIDTH` / `EDGE_VISUAL_HEIGHT` | `1280` / `720` | `show` 模式下的視窗尺寸（px）。|
| `EDGE_INGEST_MODE` | `rtsp` | 取流模式：`rtsp` 或 `file`。|
| `EDGE_FILE_PATH` | *(不設定)* | `file` 模式時必填，指向本地或掛載 volume 的影片路徑。|
| `EDGE_FILE_LOOP` | `1` | 影片結束後是否自動從頭播放，模擬無限串流。|
| `EDGE_FILE_FPS` | *(不設定)* | `file` 模式下覆寫 workflow 節奏的 FPS；未設定則回退 RTSP FPS 或 `EDGE_POLL_INTERVAL`。|
| `EDGE_FILE_DROP_FRAMES` | *(沿用 `EDGE_RTSP_DROP_FRAMES` 預設)* | 影片模式下每圈捨棄的影格數。|
| `EDGE_RTSP_URL` | `rtsp://localhost:554/stream` | RTSP 模式的串流來源 URL。|
| `EDGE_RTSP_DROP_FRAMES` | `2` | 每圈讀取時丟棄的舊影格數，越大越能接近最新畫面。|
| `EDGE_RTSP_FPS` | `30` | 目標串流 FPS，決定取流節奏；設定 `0` 表示不節流。|
| `EDGE_RTSP_WIDTH` / `EDGE_RTSP_HEIGHT` | *(不設定)* | 若需指定 RTSP 解碼後的影格解析度，可設定此二值。|
| `EDGE_RTSP_RECONNECT` | `1` 秒 | RTSP 連線失敗後重新連線的等待秒數。|
| `EDGE_POLL_INTERVAL` | `5` 秒 | Workflow 迴圈等待秒數。|
| `INTEGRATION_API_BASE` | `http://localhost:9000` | 模擬整合端 API 伺服器。|
| `MONITOR_ENDPOINT` | `http://localhost:9400` | monitoring sidecar base URL（亦接受 `/events` 完整路徑）。|
| `EDGE_MONITOR_SERVICE_NAME` | `edge-{EDGE_CAMERA_ID}` | 上報到 monitoring server 的服務名稱。|
| `EDGE_LOG_LEVEL` | `INFO` | 控制 edge 服務的 logging level，亦可透過 `LOG_LEVEL` 覆寫。|

> PipelineScheduler 會優先依取流模式的 FPS（`EDGE_FILE_FPS` 或 `EDGE_RTSP_FPS`）控制每輪 workflow 節奏；若設為 0，則退回使用 `EDGE_POLL_INTERVAL`。
可依實際部署（Docker、K8s）調整。

### Logging 與錯誤處理

- Ingestion 任務在初始化/斷線/EOF 時會留下詳細 log，`EDGE_RTSP_RECONNECT` 決定 RTSP 重新連線前的等待秒數；檔案模式則在每次迴圈播放時記錄 rewind。
- `PipelineScheduler` 每圈會輸出實際耗時與 sleep 秒數，可搭配 `EDGE_LOG_LEVEL=DEBUG`（或通用 `LOG_LEVEL`）觀察節奏。
- 若來源無法恢復，任務會拋 `TaskError`，WorkflowRunner 依 backoff 策略（`EDGE_RETRY_BACKOFF`）重試並透過 Monitoring 送出失敗事件。

### 測試與品質保證

- 更詳細的測試層級、lint/type check 建議與 `.env` 測試設定請見 [`edge/docs/TESTING.md`](docs/TESTING.md)。

### 同時啟動多個 Edge 實例

`edge/scripts/run_all.sh` 會遍歷 `env/.env.*` 檔案並依序啟動多個 edge 節點，按 `Ctrl+C` 會自動結束所有子程序：

```bash
cd edge
cp env/cam01.env.example env/.env.cam01
cp env/cam01.env.example env/.env.cam02
# 調整各檔案內容...

./scripts/run_all.sh           # 讀取 env/.env.*
./scripts.run_all.sh '.env.ca?'  # (可選) 使用自訂樣式
```

### Docker Compose 部署

`edge/docker-compose.yml` 專門用於容器化 edge 服務。步驟：

```bash
cd edge
cp env/cam01.env.example env/.env.cam01
cp env/cam02.env.example env/.env.cam02   # 視需求新增更多
# 調整 env/.env.camXX（MONITOR_ENDPOINT/INTEGRATION_API_BASE/RTSP URL 等）

docker compose up --build              # 只啟動 cam01
docker compose --profile cam02 up -d   # 如需同時啟動 cam02 profile
```

映像將 `edge/` 原始碼複製至容器並於建置時執行 `pip install -e /svc/edge`，啟動時會在 `/svc/edge` 內執行 `python main.py`。確保 `.env` 檔內的 `MONITOR_ENDPOINT`、`INTEGRATION_API_BASE` 以及 `EDGE_RTSP_URL` / `EDGE_FILE_PATH` 等變數指向容器可連線/可讀的來源。如需觀察輸出影像，可額外將 `edge/output_frames` 掛載為 volume。

> 共用網路：此 compose 會連到名為 `smartware_net` 的外部 network。若尚未啟動 streaming 組態，請先 `docker network create smartware_net` 再部署。

> GPU 注意事項：`docker-compose.yml` 已使用 `deploy.resources.reservations.devices` 以及 `NVIDIA_VISIBLE_DEVICES=all` 來請求一張 NVIDIA GPU。請確認主機已安裝 NVIDIA Container Toolkit，並以 `docker compose`（非 swarm）執行，否則需要依環境調整 runtime/deploy 設定。

## 待辦 / 擴充方向

- 將 `FrameDecodeTask` 改為實際 ffmpeg/OpenCV 解碼，輸出 numpy frame。
- `InferenceTask` 接上 Ultralytics API 或 TensorRT 模型，並將結果序列化。
- `PublishResultTask` 實作 Kafka / MQTT / REST 傳輸。
- 與 `writer/client` 專案的 pipeline 對齊（管理 API、健康檢查、probe 事件等）。
