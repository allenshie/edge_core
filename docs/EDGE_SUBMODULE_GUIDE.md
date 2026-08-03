# Edge Core 外部專案整合指南

本文件說明如何在其他專案中使用 `edge_core`，並在使用端專案內管理模型、排程檔與 `edge_mode` 切換。

## 建議整合方式

- 將 `edge_core` 作為依賴套件安裝
- 由使用端專案提供具體模型實作類、`schedule.json`、`configs/`、`weights/` 與 runtime `.env`

如果你的發佈流程偏向 source checkout，也可以把 `edge_core` 以子模組方式納入使用端專案，但這不是唯一做法。

## 建議專案結構

```text
my_edge_project/
├─ configs/
├─ schedules/
├─ weights/
├─ src/
│  └─ <app_pkg>/
│     └─ models/           # 自訂模型實作類
├─ .env                    # 使用端專案環境設定
└─ main.py                 # 專案啟動入口（可選）
```

## 啟動入口

`main.py` 的責任通常只有兩件：

- 載入使用端專案的 `.env`
- 呼叫 `edge.main.main()`

```python
from pathlib import Path
import os


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

若使用端專案已經提供自己的 console script，也可以不另外寫 `main.py`。

## `.env` 建議欄位

```ini
EDGE_RESOURCE_ROOT=.
EDGE_SCHEDULE_PATH=./schedules/schedule.json
EDGE_MODE_DEFAULT=working_stage_1
INFERENCE_ENGINE_CLASS=edge.pipeline.tasks.inference.scheduled:ScheduledInferenceEngine
EDGE_APP_INBOUND_BACKEND=mqtt
EDGE_PHASE_ENABLED=1
EDGE_PHASE_CHANNEL=integration/phase
EDGE_PHASE_RESOURCE_NAME=edge_mode
EDGE_EVENTS_BACKEND=http
EDGE_EVENTS_CHANNEL=/edge/events
EDGE_MATCHING_RESULT_ENABLED=1
EDGE_MATCHING_RESULT_CHANNEL=integration/matching
EDGE_MATCHING_RESULT_RESOURCE_NAME=matching_result_snapshot
```

重點如下：

- `EDGE_RESOURCE_ROOT` 讓排程檔、權重與其他相對路徑以使用端專案根目錄解析
- `EDGE_APP_INBOUND_BACKEND` 為 phase / matching 共用的 inbound backend
- `EDGE_PHASE_*`、`EDGE_MATCHING_RESULT_*`、`EDGE_EVENTS_*` 分別控制 route-based messaging
- `EDGE_MQTT_*` 僅提供 MQTT 協議連線參數

## 模型類規範

- 需要額外狀態判斷的模型，建議由使用端專案自行實作
- 其他模型類可直接使用 `edge_core` 提供的共通類：
  - `edge.pipeline.tasks.inference.models.YoloDetectionModel`
  - `edge.pipeline.tasks.inference.models.YoloPoseModel`
  - `edge.pipeline.tasks.inference.models.BaseYamlMockModel`
- `model_class` 應指向實際可載入的模組路徑，例如：
  - `edge.pipeline.tasks.inference.models:YoloDetectionModel`
  - `edge.pipeline.tasks.inference.models:YoloPoseModel`
  - `edge.pipeline.tasks.inference.models:BaseYamlMockModel`
  - `<app_pkg>.models.iron_gate_state:IronGateStateModel`
  - `<app_pkg>.models.cargo_pose:CargoPoseModel`
- 若 `model_class` 使用 `edge.pipeline.tasks.inference.models:BaseYamlMockModel`，`schedule.json` 需明確提供 `env_var` 與 `default_config_path`
- `EdgeDetection` 欄位定義請見 [DETECTIONS.md](DETECTIONS.md)

## phase / matching 切換

`edge_core` 啟動後不再提供獨立的 `/mode` API。
phase 會由整合端透過 `EDGE_PHASE_*` 設定的 inbound route 更新。

engine 內可透過 `context.get_resource("edge_mode")` 取得目前 mode；若啟用 matching debug，`matching_result_snapshot` 會寫入對應的 `TaskContext` resource，`StreamingTask` 會以 `g:x, l:y` 的 label 呈現。

## 測試建議

1. 先用 `EDGE_MODE_DEFAULT` 確認排程是否能切換
2. 再用對應的 app backend route 模擬 phase 更新
3. 若啟用 matching debug，確認 `matching_result_snapshot` 會被更新並反映到 streaming label
4. 若使用 `ScheduledInferenceEngine`，同步確認 `schedule.json` 與 `configs/models.yaml` 可被正確讀取
