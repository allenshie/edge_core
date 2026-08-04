# 串流設定與 MediaMTX 驗證

`edge_core` 的串流輸出是選配功能。啟用後，`StreamingTask` 會先完成疊圖，再將影格交給 FFmpeg，最後推送到外部串流伺服器。

本文件只整理串流輸出相關的環境變數與本地驗證方式；完整環境變數總表請參考 [ENV.md](ENV.md)。

## 前置條件

- 已完成 `edge_core/env/` runtime env 檔設定
- 已準備可接收 RTMP 推流的 MediaMTX 伺服器
- 若要本地驗證播放，建議同時安裝 `ffplay` 或其他 RTSP 播放工具

## 串流環境變數

下表只列出與串流輸出直接相關的設定。

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EDGE_STREAMING_ENABLED` | `false` | 是否啟用串流輸出。程式會將 `true/false`、`1/0` 視為布林值；範例與模板建議維持 `true/false`。 |
| `EDGE_STREAMING_URL` | *(空字串)* | 推流目標 URL，通常為 RTMP endpoint。 |
| `EDGE_STREAMING_STRATEGY` | `cpu` | 編碼策略：`cpu` 使用 `libx264`，`gpu` 使用 `h264_nvenc`。 |
| `EDGE_STREAMING_FPS` | *(未設定)* | 推流輸出 FPS，與取流節奏獨立。 |
| `EDGE_STREAMING_OUT_WIDTH` | `1280` | 串流輸出縮放寬。 |
| `EDGE_STREAMING_OUT_HEIGHT` | `720` | 串流輸出縮放高。兩者必須同時設定才會生效。 |
| `EDGE_STREAMING_QUEUE_SIZE` | `30` | 串流 queue 長度。 |
| `EDGE_STREAMING_IDLE_TIMEOUT` | `3` | 無幀超時秒數；超時後會停流並關閉 FFmpeg。 |
| `EDGE_STREAMING_RESTART_BACKOFF` | `1` | FFmpeg 重啟最小間隔秒數。 |
| `EDGE_STREAMING_SHM_MB` | `30` | `ShmStreamingEngine` 使用的共享記憶體大小（MB）。 |
| `STREAMING_ENGINE_CLASS` | *(未設定)* | 自訂串流引擎 class path。 |

若你也需要錄影功能，請參考 [ENV.md](ENV.md) 中的 `EDGE_STREAMING_RECORD_*` 設定。

## 啟動 MediaMTX

本機最小啟動指令如下：

```bash
docker run --rm -it \
  -p 8554:8554 \
  -p 1935:1935 \
  -p 8888:8888 \
  bluenviron/mediamtx:latest
```

預設會開啟：

- `1935`：RTMP 推流
- `8554`：RTSP 播放
- `8888`：HLS / Web 相關入口

## 推流目標

`edge_core` 的串流輸出 URL 通常寫成：

```env
EDGE_STREAMING_URL=rtmp://127.0.0.1:1935/live/cam01
```

其中 `cam01` 可依實際相機編號或 stream key 調整。

## 建議的最小設定

若目標是先把本地推流跑起來，可先使用以下設定：

```env
EDGE_STREAMING_ENABLED=true
EDGE_STREAMING_URL=rtmp://127.0.0.1:1935/live/cam01
EDGE_STREAMING_STRATEGY=cpu
EDGE_STREAMING_OUT_WIDTH=1280
EDGE_STREAMING_OUT_HEIGHT=720
EDGE_STREAMING_QUEUE_SIZE=30
EDGE_STREAMING_IDLE_TIMEOUT=3
EDGE_STREAMING_RESTART_BACKOFF=1
```

`EDGE_STREAMING_OUT_WIDTH` 與 `EDGE_STREAMING_OUT_HEIGHT` 必須同時設定才會啟用縮放。

## 本地驗證

若推流正常，可用 RTSP 播放器觀察輸出：

```bash
ffplay -rtsp_transport tcp -fflags nobuffer -flags low_delay -framedrop -probesize 32 -analyzeduration 0 \
  rtsp://127.0.0.1:8554/live/cam01
```

## 相關文件

- [ENV.md](ENV.md)
- [HEALTH.md](HEALTH.md)
- [OPERATIONS.md](OPERATIONS.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
