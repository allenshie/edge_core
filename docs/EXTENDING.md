## 自訂 Inference / Publish 與 Mode 控制

### 自訂 Inference Engine

- 撰寫繼承 `BaseInferenceEngine` 的類別，實作 `process(context)`
- 回傳 `list[EdgeDetection]`
- 在 `.env` 內設定 `INFERENCE_ENGINE_CLASS=package.module:Class`

### 自訂 Publish Engine

- 撰寫繼承 `BasePublishEngine` 的類別，實作 `publish(context, detections)`
- 在 `.env` 內設定 `PUBLISH_ENGINE_CLASS=package.module:Class`

### Mode 控制

- edge-core 啟動後會提供 `POST /mode` 介面
- 自訂引擎可透過 `context.get_resource("edge_mode")` 取得目前模式

```bash
curl -X POST http://<EDGE_MODE_SERVER_HOST>:<EDGE_MODE_SERVER_PORT>/mode \
     -H "Content-Type: application/json" \
     -d '{"mode": "working"}'
```
