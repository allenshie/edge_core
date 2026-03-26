## Edge Core 整合指南

本文件說明如何在上層專案或 site repo 中使用 `edge_core`，並在專案內管理
模型、排程檔與 `edge_mode` 切換。

目前建議主路徑：

- `edge_core` 作為依賴套件安裝
- 專案本身作為 site repo / 主專案套件，承接具體模型實作類

Git submodule 方式仍可行，但不再是唯一推薦方案。

### 1) 建議專案結構

```
my_edge_project/
├─ configs/
├─ schedules/
├─ weights/
├─ src/
│  └─ <site_pkg>/
│     └─ models/           # 自訂模型實作類
├─ .env                    # 主專案環境設定
└─ main.py                 # 專案啟動入口（可選）
```

### 2) 主專案啟動入口

`main.py` 負責：

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
    load_env(Path(__file__).resolve().parent / ".env")

    from edge.main import main as edge_main

    edge_main()


if __name__ == "__main__":
    main()
```

若專案本身已在環境中安裝並提供 console script，可不需要自寫 `main.py`。

### 3) 主專案 .env 建議欄位

```ini
EDGE_RESOURCE_ROOT=.
EDGE_SCHEDULE_PATH=./schedules/schedule.json
EDGE_MODE_DEFAULT=working_stage_1
INFERENCE_ENGINE_CLASS=edge.pipeline.tasks.inference.scheduled:ScheduledInferenceEngine
EDGE_PHASE_BACKEND=mqtt
EDGE_PHASE_CHANNEL=integration/phase
EDGE_EVENTS_BACKEND=http
EDGE_EVENTS_CHANNEL=/edge/events
```

> `EDGE_RESOURCE_ROOT` 讓排程檔/權重/其他相對路徑統一以主專案根目錄解析。
> `schedule.json` 與 `configs/models.yaml` 的詳細格式請見 `SCHEDULED_INFERENCE.md`。
> `EDGE_PHASE_*` / `EDGE_EVENTS_*` 為 route-based messaging 設定；`EDGE_MQTT_*` 僅提供 MQTT 協議連線參數。

### 4) site repo 內模型類規範

- 具體模型類建議直接繼承 `edge_core` 提供的共通類：
  - `edge.pipeline.tasks.inference.models.YoloDetectionModel`
  - `edge.pipeline.tasks.inference.models.YoloPoseModel`
  - `edge.pipeline.tasks.inference.models.BaseYamlMockModel`
- `model_class` 應指向專案套件內的路徑，例如：
  - `<site_pkg>.models.detection_tracking:DetectionTrackingModel`
  - `<site_pkg>.models.cargo_pose:CargoPoseModel`
- `EdgeDetection` 欄位定義請見 `edge/docs/DETECTIONS.md`

### 5) edge_mode 切換

edge-core 啟動後會提供 `/mode` API，供 integration 端切換模式：

```bash
curl -X POST http://<edge-host>:9100/mode \
     -H "Content-Type: application/json" \
     -d '{"mode": "working_stage_2"}'
```

engine 內可透過 `context.get_resource("edge_mode")` 取得目前 mode。

### 6) 測試建議

1) 先用 `EDGE_MODE_DEFAULT` 確認排程是否能切換  
2) 再用 `curl /mode` 模擬 integration 端切換  
3) 檢查 log 是否有 phase 變更與任務執行  
4) 若使用 `ScheduledInferenceEngine`，同步確認 `schedule.json` 與 `configs/models.yaml` 可被正確讀取
