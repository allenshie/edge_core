# Edge .env 範本

此目錄用於存放各攝影機節點的環境變數檔案，可依照 `cam01.env.example`、`cam02.env.example` 內容複製成多份 `env/.env.camXX`，再於啟動前載入；若需模擬串流，可將 `EDGE_INGEST_MODE=file` 並設定 `EDGE_FILE_PATH` 指向本地 MP4：

```bash
cd edge
cp env/cam01.env.example env/.env.cam01
cp env/cam02.env.example env/.env.cam02
vim env/.env.cam02  # 調整 camera_id/RTSP URL/monitor service name

set -a
source env/.env.cam02
set +a
python -m edge.main
```

若使用 Docker/K8s，可直接於 compose 中引用 `env/.env.camXX` 作為 `env_file`，或轉換為 ConfigMap，確保每個 edge 實例擁有獨立的攝影機設定。
