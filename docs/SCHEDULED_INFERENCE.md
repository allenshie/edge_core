## ScheduledInferenceEngine 使用說明

本文件說明使用 `edge.pipeline.tasks.inference.scheduled:ScheduledInferenceEngine`
時，主專案或 site repo 需要準備哪些檔案與設定。

### 1. 必要環境變數

```env
EDGE_RESOURCE_ROOT=.
EDGE_SCHEDULE_PATH=./schedules/schedule.json
INFERENCE_ENGINE_CLASS=edge.pipeline.tasks.inference.scheduled:ScheduledInferenceEngine
```

- `EDGE_RESOURCE_ROOT`：
  相對路徑解析根目錄。`schedule.json`、`weights_path`、`configs/*` 都會以此為基準。
- `EDGE_SCHEDULE_PATH`：
  phase 排程檔位置。
- `INFERENCE_ENGINE_CLASS`：
  啟用排程式推理引擎。

### 2. 建議目錄結構

```text
my_edge_site/
├─ configs/
│  └─ models.yaml
├─ schedules/
│  └─ schedule.json
├─ weights/
├─ src/
│  └─ <site_pkg>/
│     └─ models/
└─ .env
```

### 3. schedule.json 格式

```json
{
  "working": {
    "streaming": { "enabled": true },
    "tasks": [
      {
        "name": "detect_and_track",
        "mode": "every_frame",
        "model_class": "<site_pkg>.models.detection_tracking:DetectionTrackingModel",
        "weights_path": "./weights/detect.pt"
      },
      {
        "name": "cargo_pose",
        "mode": "interval_when_idle",
        "interval_seconds": 1,
        "min_interval_seconds": 1,
        "model_class": "<site_pkg>.models.cargo_pose:CargoPoseModel",
        "weights_path": "./weights/pose.pt"
      }
    ]
  },
  "non_working": {
    "streaming": { "enabled": false },
    "tasks": [
      {
        "name": "cargo_pose",
        "mode": "replay_last",
        "source_task": "cargo_pose",
        "interval_seconds": 180
      }
    ]
  }
}
```

重點：
- `model_class` 使用 `package.module:Class` 路徑。
- `weights_path` 可省略；省略時模型會以 mock mode 初始化。
- `streaming.enabled` 會影響 `StreamingTask` 是否輸出可視化串流。

常用 `mode` 說明：

- `every_frame`
  每張影像幀都執行一次推理，適合主偵測任務。
- `interval`
  依 `interval_seconds` 固定週期執行，不要求每幀都跑。
- `interval_when_idle`
  僅在引擎判定目前沒有高優先度任務占用時執行，適合次要或較重的模型。
- `run_once_after_switch`
  當 phase/mode 切換後執行一次，適合只需在狀態切換時取樣的任務。
- `replay_last`
  不重新推理，直接重播先前任務的最後結果；需搭配 `source_task`。

實務建議：

- 主線即時偵測通常使用 `every_frame`
- 狀態輪詢或低頻任務通常使用 `interval`
- 非關鍵但耗時模型通常使用 `interval_when_idle`
- phase 切換後只需補一筆結果時使用 `run_once_after_switch`
- 不需重跑模型、只需沿用舊結果時使用 `replay_last`

### 4. 自訂模型類規範

自訂模型需繼承 `BaseInferenceModel`，或更建議繼承：

- `edge.pipeline.tasks.inference.models.YoloDetectionModel`
- `edge.pipeline.tasks.inference.models.YoloPoseModel`
- `edge.pipeline.tasks.inference.models.BaseYamlMockModel`

site 層只應保留具體實作類，例如：

- `DetectionTrackingModel(YoloDetectionModel)`
- `CargoPoseModel(YoloPoseModel)`
- `IronGateStateModel(BaseYamlMockModel)`

### 5. configs/models.yaml 用途

`configs/models.yaml` 用來提供模型共用設定。site 實作類可透過自己的
`config_loader` 讀取這份檔案，再傳給 `YoloDetectionModel` / `YoloPoseModel`。

若直接使用 `edge_core` 提供的 `YoloDetectionModel` / `YoloPoseModel`，
且未自行覆寫 `config_loader`，也會預設讀取 `EDGE_RESOURCE_ROOT/configs/models.yaml`。

範例：

```yaml
detect_and_track:
  infer_mode: track
  tracker: trackers/custom_tracker.yaml
  classes: [0, 1]
  tracked_classes: [0]
  conf: 0.25
  iou: 0.45
  verbose: false

cargo_pose:
  conf: 0.25
  iou: 0.45
  classes: [0]
  verbose: false
```

### 6. BaseYoloModel / 共通類可讀設定

`BaseYoloModel` 會處理：

- `device`
- `conf`
- `iou`
- `classes`
- `verbose`
- `imgsz`

`YoloDetectionModel` 另外支援：

- `infer_mode`
  - `predict`
  - `track`
- `tracker`
- `tracked_classes`

### 7. YAML mock 類需要的設定

若模型繼承 `BaseYamlMockModel`，通常需要：

- 對應環境變數，例如 `EDGE_IRON_GATES_CONFIG`
- 預設 config path，例如 `configs/iron_gates.yaml`

YAML 支援兩種格式：

```yaml
- class_name: iron_gate
  polygon: [[0, 0], [100, 0], [100, 100], [0, 100]]
```

或：

```yaml
camera_1:
  - class_name: iron_gate
    polygon: [[0, 0], [100, 0], [100, 100], [0, 100]]
camera_2:
  - class_name: iron_gate
    polygon: [[0, 0], [100, 0], [100, 100], [0, 100]]
```

### 8. site 層職責

若要以 site repo 管理專案，建議：

- `edge_core`：
  管理共通推理基類、共通 YOLO / YAML mock 類、推理工具函數
- `site repo`：
  管理具體實作類、`schedule.json`、`configs/`、`weights/`、`deploy/`

site repo 的模型類應直接導入 `edge_core` 共通類，不要複製一份基底層。
