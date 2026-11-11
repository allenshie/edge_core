# Changelog

## 未發布（健康回報，smart-workflow 0.2.0）

導入 [smart-health-record](https://pypi.org/project/smart-health-record/)：各節點回報健康狀態，判斷為無法恢復時結束程式，交給 systemd 或 k8s 重啟。舊的健康程式碼（`TaskHealthReporter`、健康摘要 log、`MonitoringClient` 呼叫）已移除，改用 smart_health 的燈號與 metric。

### 依賴

- smart-workflow 改為 `903e561b7304764b6af985435fea38f5bac48884`（tag `v0.2.0`），並由它帶入 `smart-health-record==0.1.1`（PyPI）。

### 新增

- 健康架構 `edge/runtime/health_topology.py`：`ingest-worker`、`ingest`、`infer`、`stream`、`stream-output`、`publish` 六個節點，見 `docs/HEALTH.md`。
- 新的 env：`EDGE_HEALTH_ENABLED`、`EDGE_HEALTH_EXIT_ON_FAILURE`、`EDGE_HEALTH_STATUS_HOST`、`EDGE_HEALTH_STATUS_PORT`、`EDGE_HEALTH_STALE_AFTER_SECONDS`、`EDGE_HEALTH_LOOP_TIMEOUT_SECONDS`、`EDGE_MQTT_KEEPALIVE`，見 `docs/ENV.md`。
- `INGESTION_ENGINE_CLASS`：替換 ingestion Engine，寫法同其他 `*_ENGINE_CLASS`，類別必須繼承 `BaseIngestionEngine`。未設定時照舊依 `EDGE_INGEST_MODE` 選擇。
- Engine 結果：`InferenceOutcome`、`StreamingStatus`、`PublishOutcome` 繼承 smart-workflow 的 `EngineResult`，多了 `result`、`reason`、`message`。既有欄位、位置參數與 `to_dict()` 都不變，`PublishOutcome(published=8, status=200)` 照常可用。
- `BaseIngestionEngine.status()`、`BaseStreamingEngine.status()`、`BaseStreamingEngine.sink_connected()`；`CaptureWorker` 新增可選參數 `on_read_begin`、`on_read_end`。
- `FrameMeta.capture_monotonic`（不送給 App、不參與比較，`to_dict()` 不變）；ingestion `fetch()` 的 dict 多一個 `capture_monotonic` 鍵。
- 模型暖機：`BaseInferenceEngine.warmup()`（預設不做事）、`ScheduledInferenceEngine.warmup()`、`DefaultInferenceEngine.warmup()`、`BaseInferenceModel.warmup()`（預設呼叫 `run()`）、`BaseYoloModel.warmup()`（用 `predict`，不經過 tracker）。
- 查詢介面：`BaseInferenceEngine.has_work_for(phase)`、`BaseStreamingEngine.wants_stream_for(phase)`、`EdgePipeline.find_node()`。
- 舊機制的計數器改成 metric：`ingest.reconnects`、`ingest.read_failures`、`publish.published`、`stream.ffmpeg_restarts`、`stream.write_failures`；Engine 新增 `BaseIngestionEngine.reconnect_count`、`read_failure_count`，`BaseStreamingEngine.ffmpeg_restarts`、`write_failures`。

### 移除

- `TaskHealthReporter`（`edge/runtime/task_health.py`）、`edge/runtime/stage_logging.py`、`edge/runtime/pipeline_summary.py`、`edge/runtime/health_contract.py`（`HealthSummaryRow`、`HealthSummaryMetrics`、`HealthSnapshotProvider`）、`edge/pipeline/tasks/ingestion/health.py`、`edge.schema.StageStats`。
- 各 NodeTask 的 `snapshot_health()`／`health_snapshot()`、`EdgePipeline.health_rows()`、`PipelineScheduler` 的 pipeline 摘要 log 與 `MonitoringClient.heartbeat` 呼叫、ingestion 的 `report_event` 呼叫。Engine 的 `health_snapshot()` 保留。
- `EdgeHealthState.mark_fatal()`、`fatal_reason`：ingestion fatal 不再讓 `/healthz` 失敗。正常模式由 smart-workflow 結束程式；**觀察模式與 `EDGE_HEALTH_ENABLED=0` 時只有 `/readyz` 變 503，k8s 不會自動重啟**（systemd 不受影響），見 `docs/HEALTH.md`。
- Engine 的 `capture_rate_meter`、`write_rate_meter`、`unique_write_rate_meter`（只供舊健康摘要使用）。`RateMeter`、`DurationMeter` 模組保留。
- env `EDGE_HEALTH_STALE_THRESHOLD_SEC`：設定了也不會生效。
- `smart_warehouse_edge` 更新 submodule 時，`tests/unit/test_pipeline_summary.py` 與 `tests/unit/test_stage_latency_metrics.py` 中使用 `build_pipeline_summary`、`snapshot_health()`、`_infer_rate` 等的測試需要刪除或修改。

### 預設行為的改變（升級前請確認）

`smart_warehouse_edge` 更新 submodule 時也會套用以下改變：

| 改變 | 原因 | 如何退回 |
|---|---|---|
| 判斷為無法恢復時以代碼 70 結束程式 | 交給 systemd 或 k8s 重啟 | `EDGE_HEALTH_EXIT_ON_FAILURE=0`（觀察模式） |
| 推理錯誤含 `illegal memory access`、`unspecified launch failure`、`device-side assert triggered` 時拋出 `FatalTaskError(cuda_unrecoverable)` | CUDA context 損壞後無法自行恢復；改版前這些錯誤和其他推理錯誤一樣只中斷這一輪並退避，之後每一輪都會失敗 | 觀察模式下不結束，照舊中斷這一輪並退避重試 |
| 啟動時暖機，啟動時間變長 | 避免第一次推理被誤判為卡住 | `EDGE_HEALTH_ENABLED=0` 時不暖機 |
| `EDGE_MQTT_KEEPALIVE` 預設 15 秒，原本固定 60 秒 | 更快發現 messaging 斷線 | 設為 `60` |
| runner 的 log 完整顯示錯誤原因 | smart-workflow 從 `b5ef4c4` 升級時帶入 `3913438`，只改 4 行 log 的格式 | 無需退回 |

其他注意事項：

- **`ValueError`：** 啟用健康回報時，`EDGE_POLL_INTERVAL + EDGE_RETRY_BACKOFF` 必須小於 `EDGE_HEALTH_LOOP_TIMEOUT_SECONDS`（預設 30），否則啟動時拋 `ValueError`。範本的 5 + 5 沒有問題。
- **`MONITOR_ENDPOINT`：** 已淘汰。設定時啟動會記一次 WARNING 並發出 `DeprecationWarning`，行為不變。
- **systemd：** `EDGE_HEALTH_ENABLED=0` 時不會送 `READY=1`／`WATCHDOG=1`，`Type=notify` 的 unit 要加 `health-disabled.conf` drop-in，見 `docs/DEPLOY_ORIN.md`。
- **k8s：** 暖機讓啟動變慢，startupProbe 的總時限建議 300 秒（`failureThreshold: 150`、`periodSeconds: 2`）。
- 停用健康回報（`EDGE_HEALTH_ENABLED=0`）時，Edge 的業務行為和導入前相同；Engine 的狀態照常更新，但沒有人讀取。例外：舊的健康摘要 log 已移除，ingestion fatal 也不再讓 `/healthz` 失敗（見上方「移除」）。

### 修正

- ingestion 的健康結果只有新畫面才算成功；沒有新畫面時為 skipped。
- publish 依發布結果記錄健康狀態；發布失敗為 failed（不中斷這一輪，和以前一樣）。
