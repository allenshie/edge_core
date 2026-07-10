# 常見故障排查

這份文件收集 `edge_core` 的第一線排查清單。若已確認設定無誤但問題仍然存在，先從這裡開始看。

## 先做的檢查

1. 確認目前載入的是哪個 `.env` 檔。
2. 確認每個 edge instance 都有獨立的 `EDGE_CAMERA_ID`。
3. 確認 `EDGE_STREAMING_URL`、`EDGE_HTTP_LISTEN_PORT`、`EDGE_MONITOR_SERVICE_NAME` 沒有跟其他 instance 衝突。
4. 確認 RTSP / MP4 / camera source 可以直接讀取。
5. 確認 MediaMTX、MQTT、integration API 等外部依賴都可連線。
6. 確認權重、config、schedule 路徑在目前工作目錄下可讀。

## 常見症狀

| 症狀 | 優先檢查 |
| --- | --- |
| `python main.py` 很快結束 | `.env` 是否存在、語法是否正確、必要變數是否缺失 |
| 找不到影格 / EOF 循環 | `EDGE_INGEST_MODE`、`EDGE_FILE_PATH`、`EDGE_RTSP_URL`、camera 權限 |
| RTSP 一直重連 | source 是否穩定、網路是否可達、`EDGE_RTSP_RECONNECT` 是否過短 |
| 串流沒有輸出 | `EDGE_STREAMING_ENABLED`、`EDGE_STREAMING_URL`、MediaMTX 是否啟動、`EDGE_STREAMING_STRATEGY` 是否為 `cpu` |
| MQTT / webhook 沒收到訊息 | `EDGE_APP_INBOUND_BACKEND`、`EDGE_PHASE_*`、`EDGE_EVENTS_*`、broker / HTTP endpoint |
| 多實例互相干擾 | 每份 env 是否都有不同的 camera id、service name、streaming URL、listen port |

## 多實例問題

如果你是透過上層專案的啟動腳本操作，先確認以下兩點：

- `scripts/run_edge.sh` 會把每個名稱對應到 `env/.env.<name>`。
- `edge_core/scripts/run_all.sh` 會掃描 `edge_core/env/.env.cam??` runtime 檔，`*.example` 只作模板。

當多個 instance 同時跑時，通常需要額外檢查：

- `EDGE_MONITOR_SERVICE_NAME`
- `EDGE_HTTP_LISTEN_PORT`
- `EDGE_STREAMING_URL`
- `EDGE_PHASE_CHANNEL`
- `EDGE_MATCHING_RESULT_CHANNEL`

## 排查順序

1. 先排除路徑錯誤與權限問題。
2. 再確認 source 是否穩定。
3. 接著檢查 messaging / streaming 外部依賴。
4. 最後再看多實例是否有資源衝突。

如果你正在驗證安裝或測試流程，請回到 [TESTING.md](TESTING.md)。
