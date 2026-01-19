## Edge Core 子模組導入指南

本文件說明如何把 `edge-core` 當作子模組導入主專案，並在主專案內管理
模型、排程檔與 `edge_mode` 切換。

### 1) 建議專案結構

```
my_edge_project/
├─ edge/                   # edge-core submodule
├─ models/                 # 自訂模型/權重
├─ schedules/              # schedule.json
├─ .env                    # 主專案環境設定
└─ main.py                 # 主專案啟動入口
```

### 2) 主專案啟動入口

`main.py` 負責：

- 將 `edge/src` 加到 `PYTHONPATH`
- 載入主專案 `.env`
- 呼叫 `edge.main.main()`

```python
from pathlib import Path
import os
import sys


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    edge_src = repo_root / "edge" / "src"
    sys.path.insert(0, str(edge_src))

    load_env(repo_root / ".env")

    from edge.main import main as edge_main

    edge_main()


if __name__ == "__main__":
    main()
```

### 3) 主專案 .env 建議欄位

```ini
EDGE_RESOURCE_ROOT=.
EDGE_SCHEDULE_PATH=./schedules/schedule.json
EDGE_MODE_DEFAULT=working_stage_1
INFERENCE_ENGINE_CLASS=edge.pipeline.tasks.inference.scheduled:ScheduledInferenceEngine
```

> `EDGE_RESOURCE_ROOT` 讓排程檔/權重/其他相對路徑統一以主專案根目錄解析。

### 4) schedule.json 範例

```json
{
  "working_stage_1": [
    {
      "name": "detect_stream",
      "mode": "every_frame",
      "model_class": "models.demo_models:DetectModel"
    },
    {
      "name": "gated_detect_scan",
      "mode": "interval",
      "interval_seconds": 300,
      "model_class": "models.demo_models:GatedDetectModel"
    }
  ],
  "working_stage_2": [
    {
      "name": "pose_snapshot",
      "mode": "run_once_after_switch",
      "model_class": "models.demo_models:PoseModel"
    }
  ]
}
```

### 5) 自訂模型 class 規範

- 模型 class 需提供 `run(frame, metadata)` 並回傳 `list[EdgeDetection]`
- 進階欄位可使用 `polygon`（segment）、`keypoints`（pose）、`category`、`extra`
- `EdgeDetection` 的欄位定義請見 `edge/docs/DETECTIONS.md`

### 6) edge_mode 切換

edge-core 啟動後會提供 `/mode` API，供 integration 端切換模式：

```bash
curl -X POST http://<edge-host>:9100/mode \
     -H "Content-Type: application/json" \
     -d '{"mode": "working_stage_2"}'
```

engine 內可透過 `context.get_resource("edge_mode")` 取得目前 mode。

### 7) 測試建議

1) 先用 `EDGE_MODE_DEFAULT` 確認排程是否能切換  
2) 再用 `curl /mode` 模擬 integration 端切換  
3) 檢查 log 是否有 phase 變更與任務執行
