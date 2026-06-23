# Edge 推理服務（edge_core）

此模組提供邊緣推理節點 runtime：取流、推理、串流輸出、事件發布。

## 與 DeepStream 的關係

`edge_core` 的設計目標不是複製 DeepStream 的實作，而是保留它的 pipeline 概念，將取流、推理、串流輸出、事件發布與狀態控制拆成可組裝的步驟。

這麼做的原因是部署條件與擴充成本不同：

- DeepStream 的 runtime 會綁定 NVIDIA 技術堆疊，例如 CUDA、TensorRT、GStreamer 與對應插件。
- 模型通常需要先轉成可部署的格式或 engine，且客製 parser / plugin 的門檻較高。
- 實務上經常會受限於驅動版本、系統相依、編碼流程與可攜性。

因此 `edge_core` 採用 Python-first 的 workflow / task 結構，保留 DeepStream 式資料流與責任切分，但把部署與擴充成本降到場域專案可接受的範圍。

### 架構差異

| 面向 | DeepStream | `edge_core` |
| --- | --- | --- |
| Pipeline 組裝 | 以 GStreamer element 與 plugin 串成串流管線 | 以 `TaskContext`、`WorkflowRunner` 與 Python tasks 組成 workflow |
| 模型部署 | 多半需轉成可部署的 engine / runtime 格式 | 可直接使用 Python inference engine / site-specific model 類別 |
| 擴充方式 | 常見是 C/C++ plugin、parser、element 擴充 | 以 Python 類別、engine、task、config 擴充 |
| 狀態傳遞 | 依賴 pipeline metadata 與 element 間流動 | 依賴 `TaskContext` resources 與 messaging subscriber |
| 部署依賴 | NVIDIA driver / CUDA / TensorRT / GStreamer plugin 生態 | 主要是 Python 專案與場域設定 |
| 適合情境 | 高吞吐、低延遲、標準化視覺分析管線 | 場域流程客製、開發迭代、可插拔控制邏輯 |

總結來說，DeepStream 偏向高效能、標準化的 GPU 視覺 runtime；`edge_core` 則偏向可維護、可配置、可逐步替換的場域流程骨架。

## 圖表導覽

如果你第一次接觸 `edge_core`，建議依照這個順序看圖：

1. [專案邊界與角色](docs/diagrams/README.md#1-project-boundary-and-roles)
2. [Runtime 啟動與關閉](docs/diagrams/README.md#2-runtime-bootstrap-and-shutdown)
3. [核心 pipeline 與 context 流](docs/diagrams/README.md#3-core-pipeline-and-context-flow)
4. [設定與擴充點](docs/diagrams/README.md#4-configuration-and-extension-points)
5. [模組依賴圖](docs/diagrams/README.md#5-module-dependency-map)

`docs/diagrams/README.md` 同時維護每張圖的 Markdown 呈現、SVG 預覽與對應的 `.mmd` 原始檔。

## Pipeline

```text
RTSP / MP4
  -> IngestionTask
  -> InferenceTask
  -> StreamingTask
  -> PublishResultTask
```

- `InferenceTask` 只做推理與輸出結果，並會讀取 `edge_mode`。
- `StreamingTask` 與其底層 engine 負責可視化與串流輸出，debug 模式下也會讀取 `matching_result_snapshot` 來呈現 `g:x, l:y`。
- `PublishResultTask` 負責推送推理事件。

## 快速啟動（獨立）

```bash
uv venv --python /usr/bin/python3.10
source .venv/bin/activate
uv pip install -e ".[vision]"
python main.py
```

## 從 site repo / 上層專案啟動（建議）

```bash
# 在 site repo 或上層專案根目錄
uv pip install -e .          # 安裝專案自己的 models / configs package
uv pip install -e "edge_core[vision]"
# 之後以專案自己的 entrypoint 啟動
```

建議做法：

- `edge_core`：提供 runtime、`ScheduledInferenceEngine`、共通 inference models
- `site repo` / 專案套件：提供具體實作類、`schedule.json`、`configs/`、`weights/`

## 串流策略

- 全域開關：`EDGE_STREAMING_ENABLED`
- phase 開關：`schedules/schedule.json` 的 `streaming.enabled`
- 推流 URL：`EDGE_STREAMING_URL`（例如 RTMP 到 MTX）
- 本機部署目前僅支援 CPU 編碼：`EDGE_STREAMING_STRATEGY=cpu`
- 無幀 watchdog：`EDGE_STREAMING_IDLE_TIMEOUT`
- 重啟退避：`EDGE_STREAMING_RESTART_BACKOFF`

詳見：`docs/ENV.md`

## 串流輸出（MediaMTX）

`StreamingTask` 會把可視化結果推送到串流伺服器。若要在本機或測試機啟用這個輸出流程，建議先部署 MediaMTX，再設定串流相關環境變數。

本機端目前僅支援 CPU 編碼，請設定 `EDGE_STREAMING_STRATEGY=cpu`。  
若要使用 GPU 編碼（NVENC），請改用容器化 GPU 環境。

1. 啟動 MediaMTX
```bash
docker run --rm -it \
  -p 8554:8554 \
  -p 1935:1935 \
  -p 8888:8888 \
  bluenviron/mediamtx:latest
```

2. 在 `.env` 或 site repo 的對應環境檔設定
```env
EDGE_STREAMING_ENABLED=true
EDGE_STREAMING_URL=rtmp://127.0.0.1:1935/live/cam01
EDGE_STREAMING_STRATEGY=cpu
# 這兩個值會在輸出前縮放影像；兩者需同時設定才會生效
EDGE_STREAMING_OUT_WIDTH=1280
EDGE_STREAMING_OUT_HEIGHT=720
EDGE_STREAMING_IDLE_TIMEOUT=3
EDGE_STREAMING_RESTART_BACKOFF=1
```

3. 播放驗證
```bash
ffplay -rtsp_transport tcp -fflags nobuffer -flags low_delay -framedrop -probesize 32 -analyzeduration 0 rtsp://127.0.0.1:8554/live/cam01
```

完整的串流環境變數與行為說明請參考：

- [設定與環境變數](docs/ENV.md)
- [串流測試（MediaMTX）](docs/ENV.md#串流測試mediamtx)

## 健康檢查（可選）

可透過環境變數啟用健康檢查 HTTP 端點：

- `EDGE_HEALTH_SERVER_ENABLED=1`
- `EDGE_HEALTH_SERVER_HOST=0.0.0.0`
- `EDGE_HEALTH_SERVER_PORT=8081`

啟用後可提供 Kubernetes probes：

- `GET /startupz`
- `GET /healthz`
- `GET /readyz`

## 參考文件

- [設定與環境變數](docs/ENV.md)
- [ScheduledInferenceEngine 使用說明](docs/SCHEDULED_INFERENCE.md)
- [設定示例（多相機）](docs/CONFIG.md)
- [自訂 Inference/Publish 與 Phase / Matching 控制](docs/EXTENDING.md)
- [Orin 部署指南（ARM）](docs/DEPLOY_ORIN.md)
- [主專案 / site repo 整合指南](docs/EDGE_SUBMODULE_GUIDE.md)
- [部署與操作（多實例、Docker）](docs/OPERATIONS.md)
- [測試與品質](docs/TESTING.md)
- [Diagrams hub](docs/diagrams/README.md)
