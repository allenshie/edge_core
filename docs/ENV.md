# Edge 環境變數說明

本文件整理 edge_core 可用的環境變數、預設值與輸入格式。

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
| `EDGE_STREAMING_ENABLED` | `0` | 是否啟用串流輸出。 |
| `EDGE_STREAMING_URL` | *(空字串)* | 推流目標 URL（通常 RTMP）。 |
| `EDGE_STREAMING_STRATEGY` | `cpu` | `cpu` (`libx264`) 或 `gpu` (`h264_nvenc`)。 |
| `EDGE_STREAMING_QUEUE_SIZE` | `30` | streaming queue 長度。 |
| `EDGE_STREAMING_IDLE_TIMEOUT` | `3` | 無幀超時秒數；超時會停流並關 ffmpeg。 |
| `EDGE_STREAMING_RESTART_BACKOFF` | `1` | ffmpeg 重啟最小間隔秒數。 |
| `EDGE_STREAMING_OUT_WIDTH` | `0` | 輸出縮放寬（0 表示不縮放）。 |
| `EDGE_STREAMING_OUT_HEIGHT` | `0` | 輸出縮放高（0 表示不縮放）。 |

## Mode 與流程控制

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_MODE_DEFAULT` | `working_stage_1` | 初始 phase。 |
| `EDGE_MODE_STRATEGY` | `external` | `external` 由整合端更新 mode。 |
| `EDGE_MODE_SERVER_ENABLED` | `0` | 是否啟用 mode HTTP server。 |
| `EDGE_MODE_SERVER_HOST` | `0.0.0.0` | mode server host。 |
| `EDGE_MODE_SERVER_PORT` | `9100` | mode server port。 |
| `EDGE_POLL_INTERVAL` | `5` | workflow loop 間隔。 |
| `EDGE_RETRY_BACKOFF` | `5` | 任務失敗重試間隔。 |

## 健康檢查（K8s Probe）

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_HEALTH_SERVER_ENABLED` | `0` | 啟用內建健康檢查 HTTP server。 |
| `EDGE_HEALTH_SERVER_HOST` | `0.0.0.0` | 健康檢查 server 綁定 host。 |
| `EDGE_HEALTH_SERVER_PORT` | `8081` | 健康檢查 server port。 |
| `EDGE_HEALTH_LIVENESS_TIMEOUT_SECONDS` | `30` | `/healthz` loop 心跳逾時門檻。 |
| `EDGE_HEALTH_READINESS_TIMEOUT_SECONDS` | `30` | `/readyz` 最近進度逾時門檻。 |
| `EDGE_HEALTH_STARTUP_GRACE_SECONDS` | `10` | startup 完成後首次 loop/progress 寬限秒數。 |

啟用後提供：
- `/startupz`：startup task 是否完成。
- `/healthz`：workflow loop 是否仍在更新。
- `/readyz`：startup 完成、近期有進度且不在 backoff。

## MQTT

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_MQTT_ENABLED` | `0` | 啟用 phase MQTT 訂閱。 |
| `EDGE_MQTT_HOST` | `localhost` | broker host。 |
| `EDGE_MQTT_PORT` | `1883` | broker port。 |
| `EDGE_PHASE_MQTT_TOPIC` | `integration/phase` | phase topic。 |
| `EDGE_MQTT_QOS` | `1` | MQTT QoS。 |
| `EDGE_MQTT_CLIENT_ID` | *(未設定)* | MQTT client id。 |
| `EDGE_MQTT_AUTH_ENABLED` | `0` | 是否啟用 MQTT 帳密驗證。 |
| `EDGE_MQTT_USERNAME` | *(未設定)* | MQTT 使用者名稱（`EDGE_MQTT_AUTH_ENABLED=1` 時必填）。 |
| `EDGE_MQTT_PASSWORD` | *(未設定)* | MQTT 密碼（建議透過 Secret 或 env 注入）。 |

## 發布與整合

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `PUBLISH_ENGINE_CLASS` | *(未設定)* | 自訂 publish 引擎 class path。 |
| `INTEGRATION_API_BASE` | `http://localhost:9000` | 整合端 API。 |
| `INTEGRATION_API_TIMEOUT` | `5` | API timeout 秒數。 |
| `EDGE_PUBLISH_BACKEND` | `http` | `http` / `mqtt` / `none`。 |
| `EDGE_EVENTS_MQTT_TOPIC` | `edge/events` | 事件 topic。 |

## 視覺化

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_VISUAL_ENABLED` | *(沿用 `EDGE_MODEL_VISUALIZE`)* | 是否啟用視覺化。 |
| `EDGE_VISUAL_MODE` | `write` | `write` 或 `show`。 |
| `EDGE_VISUAL_WINDOW` | `edge-preview` | `show` 視窗名稱。 |
| `EDGE_VISUAL_WIDTH` / `EDGE_VISUAL_HEIGHT` | `1280` / `720` | `show` 視窗尺寸。 |

## schedule.json 新格式

推薦 phase 定義：

```json
{
  "working": {
    "streaming": { "enabled": true },
    "tasks": [
      {"name": "detect_and_track", "mode": "every_frame", "model_class": "models.detection_tracking:DetectionTrackingModel"}
    ]
  },
  "non_working": {
    "streaming": { "enabled": false },
    "tasks": [
      {"name": "cargo_pose", "mode": "replay_last", "source_task": "cargo_pose", "interval_seconds": 180}
    ]
  }
}
```

相容舊格式：`{"working": [ ... ]}` 仍可讀。

## 串流測試（MediaMTX）

```bash
docker run --rm -it -p 8554:8554 -p 1935:1935 -p 8888:8888 bluenviron/mediamtx:latest
```

推流 URL 例如：

```env
EDGE_STREAMING_URL=rtmp://127.0.0.1:1935/live/cam01
```

播放：

```bash
ffplay -rtsp_transport tcp -fflags nobuffer -flags low_delay -framedrop -probesize 32 -analyzeduration 0 rtsp://127.0.0.1:8554/live/cam01
```
