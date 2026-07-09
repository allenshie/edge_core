# Edge env 使用方式

`edge_core` 子模組的批次啟動腳本會從本目錄 `env/.env.camXX` 載入每台攝影機設定。
如果你是在上層 `smart_intersection_safety_edge` 專案操作，則可改用根目錄的 `scripts/run_edge.sh`，它會讀取上層 `env/.env.camXX`。

```bash
cd edge_core
./scripts/run_all.sh
```

- `cam01` 會讀 `edge_core/env/.env.cam01`
- `cam02` 會讀 `edge_core/env/.env.cam02`

## 新增一台相機

```bash
cp env/.env.cam01 env/.env.cam05
```

再修改：
- `EDGE_CAMERA_ID`
- `EDGE_FILE_PATH` 或 `EDGE_RTSP_URL`
- `EDGE_STREAMING_URL`
- `EDGE_MONITOR_SERVICE_NAME`

## 串流建議最小設定

```env
EDGE_STREAMING_ENABLED=true
EDGE_STREAMING_URL=rtmp://127.0.0.1:1935/live/cam01
EDGE_STREAMING_STRATEGY=cpu
EDGE_STREAMING_OUT_WIDTH=1280
EDGE_STREAMING_OUT_HEIGHT=720
```

`EDGE_STREAMING_OUT_WIDTH` / `EDGE_STREAMING_OUT_HEIGHT` 會在 `_draw_detections()` 後、送入 ffmpeg 前做縮放，兩者必須同時設定才會生效。

完整變數請參考 `edge_core/docs/README.md`、`edge_core/docs/ENV.md` 與 `edge_core/docs/OPERATIONS.md`。
