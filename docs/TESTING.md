# 測試與品質保證指南

此文件說明 edge 模組在不同階段應執行的測試與品質檢查。專案不附帶影片/伺服器模擬資源，請依照 `.env` 設定提供 RTSP 或 MP4 來源進行驗證。

## 測試準備

1. 複製 `edge/.env.example` 或任一 `edge/env/*.env.example` 為新的 `.env` 檔。
2. 依測試場景選擇取流模式：
   - `EDGE_INGEST_MODE=file`：設定 `EDGE_FILE_PATH=/path/to/your/video.mp4`，必要時調整 `EDGE_FILE_FPS`、`EDGE_FILE_LOOP`。
   - `EDGE_INGEST_MODE=rtsp`：設定 `EDGE_RTSP_URL` 指向可用的 RTSP source，並確認測試環境可連線。
3. 以 `uv pip install -r requirements.txt` 安裝執行依賴；若要跑測試/靜態分析，可額外建立 `requirements-dev.txt` 以安裝 `pytest`、`pytest-mock`, `ruff`, `mypy` 等工具。

## 測試分層

### 單元測試
- `FileIngestionTask`：模擬 `cv2.VideoCapture`，驗證 EOF 迴圈/錯誤處理與 drop frame 行為。
- `RtspIngestionTask`：模擬連線失敗、frame 解碼失敗，確認會釋放 capture 並拋出 `TaskError`。
- `InferenceTask` 與 `PublishResultTask`：使用 mock 模型與 mock integration client，確認輸入/輸出與 TaskContext 資源更新正確。

執行方式（待對應測試檔案建立後）：

```bash
cd edge
pytest tests/tasks
```

### 工作流整合測試
- 以假 `TaskContext`、假 `MonitoringClient` 驗證 `InitPipelineTask` 能依 `.env` 模式建立 pipeline。
- `PipelineScheduler` 在不同 FPS 設定下會計算合理的 `sleep`，並定期送 heartbeat。
- 透過 stub inference/publish 任務，檢查 payload 是否送達整合端 mock。

範例指令：

```bash
cd edge
pytest tests/pipeline
```

### E2E smoke test
- 在 `.env` 指向實際 RTSP 或 MP4 檔案後，直接執行 `python main.py`，觀察 log 中的 ingestion/inference/publish 階段。
- 可選：搭配簡單的 mock integration server（例如以 `uvicorn scripts.mock_integration:app --reload` 啟動）驗證 HTTP 交握。

## 品質檢查

- **程式碼格式 / Lint**：建議使用 `ruff` 或 `flake8`。示例：`ruff check edge/src edge/tests`。
- **型別檢查**：以 `mypy edge/src` 確保 type hints 正確。
- **依賴鎖定**：於發佈前執行 `uv pip compile`（或 pip-tools）生成鎖檔，並在 CI 驗證可安裝。
- **CI/CD**：建立 GitHub Actions (或其他 CI) workflow，於 PR 時執行 `ruff`, `mypy`, `pytest`，必要時再加 Docker build。

## FAQ

- **為何沒有附 sample 影片/伺服器？** 由於可能涉及實際倉儲資料，專案不提供媒體或 RTSP server。請使用自行產生的測試資料，或參考公開授權的影片檔案。
- **如何同時測試多相機？** 在 `edge/env/.env.camXX` 中為每台相機定義一組設定，使用 `scripts/run_all.sh` 依序載入即可。

如需更多架構說明，可參考 `edge/README.md` 與 `SESSION.md` 中的最新進度紀錄。
