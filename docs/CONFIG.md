## Edge 設定參考

環境變數完整說明請見 [`ENV.md`](ENV.md)。
若目前專案採用 `ScheduledInferenceEngine`，請優先參考 [`SCHEDULED_INFERENCE.md`](SCHEDULED_INFERENCE.md)；
本文件保留多實例啟動示例與操作注意事項。

### 多相機 .env 範例

```bash
cp .env.example .env.cam02
set -a; source .env.cam02; set +a
python main.py
```

Messaging route 建議最少設定：

```env
EDGE_PHASE_BACKEND=mqtt
EDGE_PHASE_CHANNEL=integration/phase
EDGE_EVENTS_BACKEND=http
EDGE_EVENTS_CHANNEL=/edge/events
```

- `EDGE_PHASE_*` 控制 phase 更新來源。
- `EDGE_EVENTS_*` 控制 publish 目的地。
- 若 `EDGE_EVENTS_BACKEND=http`，channel 建議寫 `/edge/events`。
- 若 `EDGE_EVENTS_BACKEND=mqtt`，channel 建議寫 `edge/events`。

### 常用參數

若使用 `ScheduledInferenceEngine`，常用核心參數實際上是：

| 變數 | 說明 |
|------|------|
| `EDGE_RESOURCE_ROOT` | 相對路徑根目錄。 |
| `EDGE_SCHEDULE_PATH` | 指向 `schedule.json`。 |
| `INFERENCE_ENGINE_CLASS` | 設為 `edge.pipeline.tasks.inference.scheduled:ScheduledInferenceEngine`。 |
| `EDGE_CAMERA_ID` | 提供 camera-specific config 選擇。 |

其餘模型層設定建議改放到 `configs/models.yaml`，不要全部塞在 `.env`。

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `EDGE_CAMERA_ID` | `cam01` | 邊緣節點對應的相機識別（整合端映射用）。|
| `EDGE_MODEL_NAME` | `yolo11n` | 推理模型名稱（用於 log）。|
| `EDGE_MODEL_PATH` | `yolo11n.pt` | Ultralytics YOLO 權重路徑，請先下載對應 `.pt` 放到可讀位置。|
| `EDGE_MODEL_DEVICE` | *(自動)* | 指定 `cpu`、`cuda:0` 等裝置，留空由 Ultralytics 自動判斷。|
| `EDGE_MODEL_VISUALIZE` | `1` | 是否繪製推論結果（會與下列 EDGE_VISUAL_* 一併判斷）。|
| `EDGE_TRACKER_CONFIG` | *(未設定；預設使用 ByteTrack)* | Ultralytics tracker 設定檔，填 `botsort.yaml`/`bytetrack.yaml` 使用官方 cfg，或改成相對/絕對路徑指向自訂 YAML。|
| `INFERENCE_ENGINE_CLASS` | *(未設定)* | 指定自訂推理引擎類別（`package.module:Class`），需繼承 `BaseInferenceEngine`。|
| `PUBLISH_ENGINE_CLASS` | *(未設定)* | 指定 `BasePublishEngine` 子類處理推論輸出。|
| `EDGE_MODE_SERVER_HOST` / `EDGE_MODE_SERVER_PORT` | `0.0.0.0` / `9100` | mode 更新 API 的監聽位置。|
| `EDGE_MODE_DEFAULT` | `working` | 未被整合端更新時的初始 mode。|
| `EDGE_PHASE_BACKEND` / `EDGE_PHASE_CHANNEL` | `mqtt` / `integration/phase` | phase 更新 route 設定。|
| `EDGE_EVENTS_BACKEND` / `EDGE_EVENTS_CHANNEL` | `http` / `/edge/events` | edge 事件 publish route 設定。|
| `EDGE_VISUAL_ENABLED` | *(沿用 `EDGE_MODEL_VISUALIZE` 預設)* | 控制是否執行可視化輸出（write/show）。|
| `EDGE_VISUAL_MODE` | `write` | `write` 輸出檔案；`show` 使用 `cv2.imshow`。|
| `EDGE_VISUAL_WINDOW` | `edge-preview` | `show` 模式下的視窗名稱。|
| `EDGE_VISUAL_WIDTH` / `EDGE_VISUAL_HEIGHT` | `1280` / `720` | `show` 模式下的視窗尺寸（px）。|
| `EDGE_INGEST_MODE` | `rtsp` | 取流模式：`rtsp`、`file` 或 `camera`。|
| `EDGE_FILE_PATH` | *(不設定)* | `file` 模式時必填，指向影片路徑。|
| `EDGE_FILE_LOOP` | `1` | 影片結束後是否自動從頭播放。|
| `EDGE_FILE_FPS` | *(不設定)* | `file` 模式下覆寫 workflow 節奏的 FPS。|
| `EDGE_FILE_DROP_FRAMES` | *(沿用 `EDGE_RTSP_DROP_FRAMES` 預設)* | 影片模式下每圈捨棄的影格數。|
| `EDGE_CAMERA_DEVICE` | `0` | `camera` 模式時使用的 device index。|
| `EDGE_CAMERA_FPS` | *(不設定)* | `camera` 模式目標 FPS。|
| `EDGE_CAMERA_WIDTH` / `EDGE_CAMERA_HEIGHT` | *(不設定)* | `camera` 模式解析度。|
| `EDGE_CAMERA_DROP_FRAMES` | `0` | `camera` 模式每圈捨棄的影格數。|
| `EDGE_RTSP_URL` | `rtsp://localhost:554/stream` | RTSP 模式的串流來源 URL。|
| `EDGE_RTSP_DROP_FRAMES` | `2` | 每圈讀取時丟棄的舊影格數。|
| `EDGE_RTSP_FPS` | `30` | 目標串流 FPS，決定取流節奏；設 `0` 表示不節流。|
| `EDGE_RTSP_WIDTH` / `EDGE_RTSP_HEIGHT` | *(不設定)* | 指定 RTSP 解碼後影格解析度。|
| `EDGE_RTSP_RECONNECT` | `1` 秒 | RTSP 連線失敗後重新連線等待秒數。|
| `EDGE_POLL_INTERVAL` | `5` 秒 | Workflow 迴圈等待秒數。|
| `INTEGRATION_API_BASE` | `http://localhost:9000` | 模擬整合端 API 伺服器。|
| `MONITOR_ENDPOINT` | `http://localhost:9400` | monitoring sidecar base URL。|
| `EDGE_MONITOR_SERVICE_NAME` | `edge-{EDGE_CAMERA_ID}` | 上報到 monitoring server 的服務名稱。|
| `EDGE_LOG_LEVEL` | `INFO` | logging level，亦可透過 `LOG_LEVEL` 覆寫。|

### 設定注意事項

- `PipelineScheduler` 會優先依 `EDGE_FILE_FPS`、`EDGE_CAMERA_FPS` 或 `EDGE_RTSP_FPS` 控制節奏；若設為 0，回退使用 `EDGE_POLL_INTERVAL`。
- `edge/trackers/` 內已附 `bytetrack.yaml`/`botsort.yaml` 範本；若填寫相對路徑，會以 `edge` 專案根目錄解析。
- `EDGE_MQTT_*` 僅負責 broker 連線參數；phase / event 的 backend 與 channel 請改由 `EDGE_PHASE_*`、`EDGE_EVENTS_*` 設定。
- EdgeDetection 欄位定義與擴充方式請見 `edge/docs/DETECTIONS.md`。
