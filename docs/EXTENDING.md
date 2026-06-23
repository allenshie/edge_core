## 自訂 Inference / Publish 與 Phase 控制

### 自訂 Inference Engine

- 撰寫繼承 `BaseInferenceEngine` 的類別，實作 `process(context)`
- 回傳 `list[EdgeDetection]`
- 在 `.env` 內設定 `INFERENCE_ENGINE_CLASS=package.module:Class`

### 自訂 Publish Engine

- 撰寫繼承 `BasePublishEngine` 的類別，實作 `publish(context, detections)`
- 在 `.env` 內設定 `PUBLISH_ENGINE_CLASS=package.module:Class`

### Phase 控制

- edge-core 不再提供獨立的 `POST /mode` server
- phase 更新改由 `EDGE_PHASE_*` 定義的 inbound route 接收
- 若啟用 matching debug，matching 結果會寫入 `EDGE_MATCHING_RESULT_RESOURCE_NAME` 對應的 resource
- 自訂引擎可透過 `context.get_resource("edge_mode")` 或對應的 `EDGE_PHASE_RESOURCE_NAME` 取得目前 phase
- 若需要切換 phase 或 matching debug 狀態，請透過 app 端對應的 messaging route 發送更新訊息
