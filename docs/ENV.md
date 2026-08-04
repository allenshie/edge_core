# Edge 環境變數說明

本文件整理 edge_core 可用的環境變數、預設值與輸入格式。

`edge_core/env/` 另外提供三份模板檔：

- `env/.env.example`
- `env/.env.cam01.example`
- `env/.env.cam02.example`

這些檔案只作為模板與 CI 臨時複製來源；實際部署時請改用 runtime `env/.env.camXX`，不要直接把 example 當成長期輸入檔。

## 基本資訊與監控

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_CAMERA_ID` | `cam01` | 邊緣節點識別。 |
| `EDGE_LOG_LEVEL` | `INFO` | Log 等級。 |
| `MONITOR_ENDPOINT` | `http://localhost:9400` | 監控服務 endpoint。 |
| `EDGE_MONITOR_SERVICE_NAME` | `edge-{EDGE_CAMERA_ID}` | 監控服務名稱。 |

## 取流設定

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_INGEST_MODE` | `rtsp` | `rtsp` / `file` / `camera`。 |
| `EDGE_RTSP_URL` | `rtsp://localhost:554/stream` | RTSP 來源 URL。 |
| `EDGE_RTSP_DROP_FRAMES` | `2` | 每輪丟棄舊影格數。 |
| `EDGE_RTSP_RECONNECT` | `1` | RTSP 斷線重連秒數。 |
| `EDGE_RTSP_FPS` | `30` | RTSP 模式目標 FPS。 |
| `EDGE_RTSP_WIDTH` / `EDGE_RTSP_HEIGHT` | *(未設定)* | RTSP 解碼輸出解析度。 |
| `EDGE_FILE_PATH` | *(未設定)* | `file` 模式影片路徑。 |
| `EDGE_FILE_LOOP` | `1` | 影片結束後是否重播。 |
| `EDGE_FILE_FPS` | *(未設定)* | `file` 模式目標 FPS。 |
| `EDGE_FILE_DROP_FRAMES` | *(沿用 RTSP)* | `file` 模式丟幀數。 |
| `EDGE_CAMERA_DEVICE` | `0` | `camera` 模式的本機攝影機 device index。 |
| `EDGE_CAMERA_FPS` | *(未設定)* | `camera` 模式希望設定的 FPS。 |
| `EDGE_CAMERA_WIDTH` / `EDGE_CAMERA_HEIGHT` | *(未設定)* | `camera` 模式解析度設定。 |
| `EDGE_CAMERA_DROP_FRAMES` | `0` | `camera` 模式每輪丟棄舊影格數。 |

## 推理與排程

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `INFERENCE_ENGINE_CLASS` | *(未設定)* | 自訂推理引擎 class path。 |
| `EDGE_MODEL_NAME` | `yolo11n` | 單模型模式名稱。 |
| `EDGE_MODEL_PATH` | `./A6_PN001_20250806_yolov12s-dec_v1.pt` | 單模型權重。 |
| `EDGE_CONF_THRESHOLD` | `0.5` | 信心門檻。 |
| `EDGE_MODEL_DEVICE` | *(未設定)* | `cpu` / `cuda:0` 等。 |
| `EDGE_TRACKER_CONFIG` | `trackers/bytetrack.yaml` | Ultralytics tracker 設定。 |
| `EDGE_SCHEDULE_PATH` | `schedule.json` | phase 排程檔。 |
| `EDGE_MODELS_CONFIG` | `configs/models.yaml` | 模型共用設定檔路徑；`YoloDetectionModel` / `YoloPoseModel` 預設從此載入設定。 |
| `EDGE_RESOURCE_ROOT` | *(執行目錄)* | 相對路徑解析根目錄。 |

## 串流輸出（StreamingTask）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `STREAMING_ENGINE_CLASS` | *(未設定)* | 自訂串流引擎 class path。例如使用優化版：`edge.pipeline.tasks.streaming.engine:ShmStreamingEngine`。 |
| `EDGE_STREAMING_ENABLED` | `0` | 是否啟用串流輸出。 |
| `EDGE_STREAMING_URL` | *(空字串)* | 推流目標 URL（通常 RTMP）。 |
| `EDGE_STREAMING_STRATEGY` | `cpu` | `cpu` (`libx264`) 或 `gpu` (`h264_nvenc`)。 |
| `EDGE_STREAMING_FPS` | *(未設定)* | 推流輸出 FPS，與取流節奏獨立。 |
| `EDGE_STREAMING_OUT_WIDTH` | `1280` | 串流輸出縮放寬；需與 `EDGE_STREAMING_OUT_HEIGHT` 同時設定才會生效。 |
| `EDGE_STREAMING_OUT_HEIGHT` | `720` | 串流輸出縮放高；在 `_draw_detections()` 後先縮放，再送入 ffmpeg 編碼。 |
| `EDGE_STREAMING_QUEUE_SIZE` | `30` | streaming queue 長度。 |
| `EDGE_STREAMING_IDLE_TIMEOUT` | `3` | 無幀超時秒數；超時會停流並關 ffmpeg。 |
| `EDGE_STREAMING_RESTART_BACKOFF` | `1` | ffmpeg 重啟最小間隔秒數。 |
| `EDGE_STREAMING_SHM_MB` | `30` | `ShmStreamingEngine` 專用：共享記憶體大小（MB）。4K 建議 30，1080p 建議 10。 |


## Phase 與流程控制

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_MODE_DEFAULT` | `working_stage_1` | 初始 phase。 |
| `EDGE_MODE_STRATEGY` | `external` | `external` 由整合端更新 mode，搭配 `EDGE_PHASE_*` route 使用。 |
| `EDGE_POLL_INTERVAL` | `5` | workflow loop 間隔，也是 pipeline scheduler 的等待節拍；設為 `0` 時不額外 sleep，loop 以處理速度為準。 |
| `EDGE_RETRY_BACKOFF` | `5` | 任務失敗重試間隔。 |

## 健康檢查（K8s Probe）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_HEALTH_SERVER_ENABLED` | `0` | 啟用內建健康檢查 HTTP server。 |
| `EDGE_HEALTH_SERVER_HOST` | `0.0.0.0` | 健康檢查 server 綁定 host。 |
| `EDGE_HEALTH_SERVER_PORT` | `8081` | 健康檢查 server port。 |
| `EDGE_HEALTH_REPORT_INTERVAL_SEC` | `5` | 健康摘要輸出間隔秒數。 |
| `EDGE_HEALTH_STALE_THRESHOLD_SEC` | `5` | 距離上次更新超過多久視為 stale / degraded。 |
| `EDGE_HEALTH_LIVENESS_TIMEOUT_SECONDS` | `30` | `/healthz` loop 心跳逾時門檻。 |
| `EDGE_HEALTH_READINESS_TIMEOUT_SECONDS` | `30` | `/readyz` 最近進度逾時門檻。 |
| `EDGE_HEALTH_STARTUP_GRACE_SECONDS` | `10` | startup 完成後首次 loop/progress 寬限秒數。 |

端點合約、Kubernetes probe 範例與判讀規則請參考 [HEALTH.md](HEALTH.md)。

## MQTT 協議參數

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_MQTT_HOST` | `localhost` | broker host。 |
| `EDGE_MQTT_PORT` | `1883` | broker port。 |
| `EDGE_MQTT_QOS` | `1` | MQTT QoS。 |
| `EDGE_MQTT_CLIENT_ID` | *(未設定)* | MQTT client id。 |
| `EDGE_MQTT_AUTH_ENABLED` | `0` | 是否啟用 MQTT 帳密驗證。 |
| `EDGE_MQTT_USERNAME` | *(未設定)* | MQTT 使用者名稱（`EDGE_MQTT_AUTH_ENABLED=1` 時必填）。 |
| `EDGE_MQTT_PASSWORD` | *(未設定)* | MQTT 密碼（建議透過 Secret 或 env 注入）。 |
| `EDGE_MQTT_ENABLED` | `0` | 是否啟用 MQTT 協議設定；目前只影響 broker 連線/認證參數是否生效，不再用來決定 phase / matching route backend。 |

## Messaging Routes

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_APP_INBOUND_BACKEND` | `mqtt` | 供 app 端訂閱的共用 backend；`phase` 與 `matching result` 都會共用這個設定。 |
| `EDGE_PHASE_ENABLED` | `1` | 是否訂閱 phase 更新。關閉時 `ScheduledInferenceEngine` 會維持本地預設 mode。 |
| `EDGE_PHASE_CHANNEL` | `integration/phase` | phase 更新 route channel；會依 `EDGE_APP_INBOUND_BACKEND` 正規化。 |
| `EDGE_PHASE_RESOURCE_NAME` | `edge_mode` | phase/mode 寫入 `TaskContext` 的 resource key。 |
| `EDGE_EVENTS_BACKEND` | `http` | edge 推理事件 route backend；可設 `http` / `mqtt` / `none`。 |
| `EDGE_EVENTS_CHANNEL` | `/edge/events`（backend=`http`）或 `edge/events`（backend=`mqtt`） | edge 推理事件 route channel。 |
| `EDGE_MATCHING_RESULT_ENABLED` | `0` | 是否訂閱 matching broadcast；啟用後 `StreamingTask` 會改用研究模式 label（`g:x, l:y`）。 |
| `EDGE_MATCHING_RESULT_CHANNEL` | `integration/matching` | matching result route channel；會依 `EDGE_APP_INBOUND_BACKEND` 正規化。 |
| `EDGE_MATCHING_RESULT_RESOURCE_NAME` | `matching_result_snapshot` | matching snapshot 寫入 `TaskContext` 的 resource key。 |
| `EDGE_HTTP_LISTEN_HOST` | `0.0.0.0` | 當 route backend=`http` 且需要接收 webhook subscribe 時，本地 HTTP listen host。 |
| `EDGE_HTTP_LISTEN_PORT` | `9000` | 當 route backend=`http` 且需要接收 webhook subscribe 時，本地 HTTP listen port。 |

> `EDGE_APP_INBOUND_BACKEND` 只決定 app inbound route 共用的 backend；`EDGE_PHASE_ENABLED` 與 `EDGE_MATCHING_RESULT_ENABLED` 各自控制是否註冊訂閱。當所有 inbound route 都關閉時，`start_messaging_subscriber` 會直接跳過，不會額外啟動訂閱工作。

## 發布與整合

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_PUBLISH_ENABLED` | `1` | 是否啟用結果發布至外部整合端。 |
| `PUBLISH_ENGINE_CLASS` | *(未設定)* | 自訂發布引擎 class path。預設使用 `MessagingPublishEngine`。 |
| `INTEGRATION_API_BASE` | `http://localhost:9000` | 整合端 API base URL。 |
| `INTEGRATION_API_TIMEOUT` | `5` | API timeout 秒數。 |

## 疊圖（OverlayConfig / StreamingTask）

視覺輸出已由 `StreamingTask` 負責，這裡只保留疊圖樣式參數。
環境變數名稱仍沿用 `EDGE_VISUAL_*`，但語意已縮為 overlay styling。

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_VISUAL_SHOW_TRACK_INFO` | `0` | 是否在 label 顯示 `track_id`。僅在 `track_id` 不為 `None` 時附加。 |
| `EDGE_VISUAL_SHOW_SCORE_INFO` | `0` | 是否在 state label 顯示 `score`。 |
| `EDGE_VISUAL_DETECTION_COLOR` | `0,255,0` | 偵測框與 label 背景色，格式為 `B,G,R`，符合 OpenCV 色彩順序。 |

## 錄影（StreamingTask）

錄影會沿用 streaming 的最終輸出畫面，因此 `fps` / `image_size` 不另外重複設定。

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_STREAMING_RECORD_ENABLED` | `0` | 是否將最終 streaming 畫面另外錄成 mp4。 |
| `EDGE_STREAMING_RECORD_OUTPUT_DIR` | `./recordings` | 錄影輸出目錄，會自動建立。 |
| `EDGE_STREAMING_RECORD_FILENAME_TEMPLATE` | `{camera_id}_{phase}_{start_dt:%Y%m%d_%H%M%S}.mp4` | 錄影檔名樣板，可用 `camera_id`、`phase`、`start_dt` 等欄位。 |

## schedule.json 新格式

推薦 phase 定義：

```json
{
  "working": {
    "streaming": { "enabled": true },
    "tasks": [
      {"name": "detect_and_track", "mode": "every_frame", "model_class": "edge.pipeline.tasks.inference.models:YoloDetectionModel"}
    ]
  },
  "non_working": {
    "streaming": { "enabled": false },
    "tasks": [
      {"name": "cargo_pose", "mode": "replay_last", "source_task": "cargo_pose", "interval_seconds": 180, "model_class": "models.cargo_pose:CargoPoseModel"}
    ]
  }
}
```

相容舊格式：`{"working": [ ... ]}` 仍可讀。

## 串流啟動與 MediaMTX

串流輸出相關的環境變數、MediaMTX 啟動指令與本地驗證方式，請參考 [STREAMING.md](STREAMING.md)。
