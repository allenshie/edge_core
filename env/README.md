# Edge env 使用方式

`smart_warehouse_edge` 啟動腳本會從根目錄 `env/.env.camXX` 載入每台攝影機設定：

```bash
bash scripts/run_edges.sh cam01 cam02
```

- `cam01` 會讀 `env/.env.cam01`
- `cam02` 會讀 `env/.env.cam02`

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

完整變數請參考 `edge_core/docs/ENV.md`。
