# Edge env 使用方式

`edge_core/env/` 同時負責模板與 runtime env。

- `env/.env.example`：共用 baseline template
- `env/.env.cam01.example` / `env/.env.cam02.example`：相機專用 bootstrap 範本
- `env/.env.cam01` / `env/.env.cam02`：runtime 檔，只應存在於本機或部署環境

`edge_core/scripts/run_all.sh` 預設只會掃描 `env/.env.cam??`，所以 `*.example` 不會被誤當成 runtime。

## 新增一台相機

```bash
cp env/.env.cam01.example env/.env.cam05
```

然後再修改：

- `EDGE_CAMERA_ID`
- `EDGE_MONITOR_SERVICE_NAME`
- `EDGE_RTSP_URL` 或 `EDGE_FILE_PATH`
- `EDGE_STREAMING_URL`

## 以單一實例啟動

```bash
set -a
source env/.env.cam01
set +a
python main.py
```

## 批次啟動多實例

```bash
./scripts/run_all.sh
./scripts/run_all.sh '.env.cam0?'  # 可選：自訂 pattern
```

## 串流建議最小設定

```env
EDGE_STREAMING_ENABLED=true
EDGE_STREAMING_URL=rtmp://127.0.0.1:1935/live/cam01
EDGE_STREAMING_STRATEGY=cpu
EDGE_STREAMING_OUT_WIDTH=1280
EDGE_STREAMING_OUT_HEIGHT=720
```

`EDGE_STREAMING_OUT_WIDTH` / `EDGE_STREAMING_OUT_HEIGHT` 會在 `_draw_detections()` 後、送入 ffmpeg 前做縮放，兩者必須同時設定才會生效。

串流啟動與 MediaMTX 驗證請參考 `edge_core/docs/STREAMING.md`。

完整變數請參考 `edge_core/docs/README.md`、`edge_core/docs/ENV.md`、`edge_core/docs/STREAMING.md` 與 `edge_core/docs/OPERATIONS.md`。
