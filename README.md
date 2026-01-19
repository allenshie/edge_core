# Edge 推理服務

此模組提供邊緣推理節點骨架：取流 → 推理 → 發佈事件 → 監控回報。支援
RTSP 與檔案模式，並可透過插件化引擎擴充推理與輸出行為。

## 流程概述

```text
RTSP / MP4 → IngestionTask → InferenceTask → PublishResultTask → integration
```

## 快速啟動

```bash
cd edge
cp .env.example .env
uv venv --python /usr/bin/python3.12  # 或 python -m venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -e .
python main.py
```

> 請先調整 `.env` 的取流來源、模型權重、整合端位址等必要參數。

## 參考文件

- [設定與環境變數](docs/CONFIG.md)
- [自訂 Inference/Publish 與 Mode 控制](docs/EXTENDING.md)
- [子模組導入主專案](docs/EDGE_SUBMODULE_GUIDE.md)
- [EdgeDetection 格式](docs/DETECTIONS.md)
- [部署與操作（多實例、Docker）](docs/OPERATIONS.md)
- [測試與品質](docs/TESTING.md)
