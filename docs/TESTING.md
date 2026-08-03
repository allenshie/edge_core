# 測試與品質保證指南

此文件說明 edge 模組在不同階段應執行的測試與品質檢查。專案不附帶影片/伺服器模擬資源，請依照 runtime env 設定提供 RTSP 或 MP4 來源進行驗證。

## 測試準備

1. 複製 `env/.env.cam01.example` 為新的 `env/.env.cam01`，或在你的整合專案內建立專案自己的 runtime `.env`。
2. 若要同時測多台相機，再額外複製 `env/.env.cam02.example` 為 `env/.env.cam02`。
3. 依測試場景選擇取流模式：
   - `EDGE_INGEST_MODE=file`：設定 `EDGE_FILE_PATH=/path/to/your/video.mp4`，必要時調整 `EDGE_FILE_FPS`、`EDGE_FILE_LOOP`。
   - `EDGE_INGEST_MODE=rtsp`：設定 `EDGE_RTSP_URL` 指向可用的 RTSP source，並確認測試環境可連線。
   - `EDGE_INGEST_MODE=camera`：設定 `EDGE_CAMERA_DEVICE=0`（或其他 device index），可選擇調整 `EDGE_CAMERA_FPS`、`EDGE_CAMERA_WIDTH`、`EDGE_CAMERA_HEIGHT`。
4. 以 `uv pip install -e ".[vision]"` 或專案實際安裝方式安裝執行依賴；若要跑測試/靜態分析，可額外建立 dev 依賴安裝 `pytest`、`ruff`、`mypy` 等工具。
5. 若測 messaging，請優先使用新變數：
   - `EDGE_APP_INBOUND_BACKEND`
   - `EDGE_PHASE_ENABLED` / `EDGE_PHASE_CHANNEL` / `EDGE_PHASE_RESOURCE_NAME`
   - `EDGE_MATCHING_RESULT_ENABLED` / `EDGE_MATCHING_RESULT_CHANNEL` / `EDGE_MATCHING_RESULT_RESOURCE_NAME`
   - `EDGE_EVENTS_BACKEND` / `EDGE_EVENTS_CHANNEL`

## 測試分層

### 單元測試
- `FileIngestionEngine`：模擬 `cv2.VideoCapture`，驗證 EOF 迴圈/錯誤處理與 drop frame 行為。
- `RtspIngestionEngine`：模擬連線失敗、frame 解碼失敗，確認會釋放 capture 並拋出 `TaskError`。
- `CameraIngestionEngine`：模擬本機 camera 開啟失敗與 drop frame 行為。
- `InferenceTask` 與 `PublishResultTask`：使用 mock 模型與 mock integration client，確認輸入/輸出與 TaskContext 資源更新正確。

執行方式：

```bash
pytest tests -q
```

### Mock 整合測試
- 以假 `TaskContext`、假 `MonitoringClient`、假 `MessagingClient` 驗證 bootstrap / subscriber wiring 可在沒有 GPU、沒有權重、沒有 RTSP source 的情況下完成。
- 驗證 `EdgeConfig` 讀入 `env/.env.camXX.example` 後，`build_context()`、`init_messaging_client()` 與 `start_messaging_subscriber()` 的最小協作流程可正常運作。
- 透過 stub inference / publish 任務，檢查 payload 與 resource 更新是否進入預期 state。

範例指令：

```bash
pytest tests/integration
```

### E2E smoke test
- 在 `.env` 指向實際 RTSP 或 MP4 檔案後，直接執行 `python main.py` 或專案自己的 entrypoint，觀察 log 中的 ingestion/inference/publish 階段。
- 若暫時沒有 RTSP source，可使用 `EDGE_INGEST_MODE=camera` 以本機 webcam/USB camera 做 live source 驗證。
- 可選：搭配簡單的 mock integration server（例如以 `uvicorn scripts.mock_integration:app --reload` 啟動）驗證 HTTP 交握。
- 若 phase 與 matching 走 MQTT、events 走 HTTP，建議至少驗證一次：
  - `EDGE_APP_INBOUND_BACKEND=mqtt`
  - `EDGE_PHASE_ENABLED=1`
  - `EDGE_MATCHING_RESULT_ENABLED=1`
  - `EDGE_EVENTS_BACKEND=http`
  以確認 route-based messaging 配置正確。

## CI 範圍

CI 只保留可無 GPU / 無真實場域資源就能執行的檢查：

- `ruff`
- `pytest tests/`
- `pytest edge_core/tests/`
- `docker compose config`

CI 不包含：

- 完整推理流
- 真實權重檔
- 真實 RTSP / camera source
- GPU 推理或編碼
- Docker image push / GitOps 更新

## 品質檢查

- **程式碼格式 / Lint**：建議使用 `ruff` 或 `flake8`。示例：`ruff check edge/src edge/tests`。
- **型別檢查**：以 `mypy edge/src` 確保 type hints 正確。
- **依賴鎖定**：於發佈前執行 `uv pip compile`（或 pip-tools）生成鎖檔，並在 CI 驗證可安裝。
- **CI/CD**：建立 GitHub Actions (或其他 CI) workflow，於 PR 時執行 `ruff`, `mypy`, `pytest`，必要時再加 Docker build。

## 常見排查

- 需要 sample 影片或 RTSP server 時，請使用自行產生的測試資料或公開授權的媒體，不要假設 repo 內會附測資。
- 多相機測試請先參考 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 的多實例檢查清單，再依 [OPERATIONS.md](OPERATIONS.md) 啟動各自的 `.env.camXX`。

如需更多測試準備與常見問題，請先看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)，再回到 `README.md`、`SCHEDULED_INFERENCE.md` 與 `ENV.md`。
