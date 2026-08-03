# Edge 推理服務（edge_core）

`edge_core` 是邊緣推理節點的 runtime 套件，負責取流、推理、串流輸出、事件發布與 health probe。它以 Python workflow / task 組裝 pipeline，讓使用端專案能透過設定與自訂類別擴充行為。

## Overview

- 核心流程：`RTSP / MP4 / Camera -> IngestionTask -> InferenceTask -> StreamingTask -> PublishResultTask`
- 支援 phase / schedule 驅動的推論與串流控制
- 支援可選的 K8s health server
- 支援 MQTT / HTTP / file-based 的場域整合方式
- 可由使用端專案提供自訂 model / engine / publish / streaming 類別

## Quick Start

```bash
uv venv --python /usr/bin/python3.10
source .venv/bin/activate
uv pip install -e ".[vision]"
```

接著準備 runtime 設定並啟動：

```bash
cp env/.env.cam01.example env/.env.cam01
# 視需要調整 EDGE_...、schedule.json、paths
python main.py
```

多實例啟動請使用 `scripts/run_all.sh` 搭配 `env/.env.camXX`。
若要設定串流、health probe 或部署方式，請參考下方文件。

## Reference

- [文件索引](docs/README.md)
- [設計理念與 DeepStream 的關係](docs/EXPLANATION.md)
- [環境變數](docs/ENV.md)
- [健康檢查與 Kubernetes Probe](docs/HEALTH.md)
- [部署與操作](docs/OPERATIONS.md)
- [常見故障排查](docs/TROUBLESHOOTING.md)
- [ScheduledInferenceEngine 使用說明](docs/SCHEDULED_INFERENCE.md)
- [設定示例（多相機）](docs/CONFIG.md)
- [自訂 Inference/Publish 與 Phase / Matching 控制](docs/EXTENDING.md)
- [外部專案整合指南](docs/EDGE_SUBMODULE_GUIDE.md)
- [測試與品質](docs/TESTING.md)
- [env 目錄使用方式](env/README.md)
- [Diagrams hub](docs/diagrams/README.md)
