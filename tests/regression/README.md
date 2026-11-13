# 業務輸出回歸測試

健康回報只觀察業務，不能改變業務的結果：導入健康回報前後，送給 messaging client 的 topic、MQTT 設定與 payload 必須相同。允許的差異只有兩項：

- **`EDGE_MQTT_KEEPALIVE`：** 預設從固定 60 秒改為 15 秒，為了更快發現 messaging 斷線（需要原本的行為時設為 `60`）。所以比對 MQTT 設定時不含 `keepalive`。
- **暖機造成的畫面差異：** 啟用健康回報時，啟動任務會用空白畫面讓每個模型先推理一次，避免第一次推理太久被誤判為卡住。空白畫面不是業務畫面，所以只比對假模型收到的非空白畫面。用真實模型與檔案來源時，暖機也會讓第一次推理變快，原本因第一次推理較慢而略過的畫面不再略過；這裡的假取流依呼叫次數回傳畫面、不受推理時間影響，所以沒有這個差異。`EDGE_HEALTH_ENABLED=0` 時不暖機，畫面和導入前相同。

- `business_fakes.py`：假的取流 Engine（依 `SCRIPT` 回傳 12 次畫面，包含沒有新畫面與 3 次 phase 切換）、兩個無狀態的假模型、假的 `MessagingClient`。
- `harness.py`：替換 `ENGINE_BY_MODE["file"]` 與 `edge.messaging.MessagingClient`，執行真的 `edge.main.run_daemon()`；推理用真的 `ScheduledInferenceEngine`（`data/schedule.json`），發布用真的 `MessagingPublishEngine`。只使用導入前就存在的介面。
- 比對內容：`routes`（route 與 topic）、`mqtt`（qos、retain 等，**不含 `keepalive`**，見上方允許的差異）、每次 publish 的 fetch 序號、route 與 payload（`timestamp` 與 `session_id` 以固定字串取代）、假模型收到的非空白畫面。
- `test_business_regression.py`：`EDGE_HEALTH_ENABLED=1`、`EDGE_HEALTH_ENABLED=0`、觀察模式（`EDGE_HEALTH_EXIT_ON_FAILURE=0`）三種都要和基準相同，而且不能呼叫結束程式。

## 基準資料

`data/business_baseline.json` 在導入前的環境錄製：

- edge_core `5422b1e`（`git archive` 取出，`diff -r` 確認和 `5422b1e` 的 `src/` 相同）
- smart-workflow 0.1.0（`b5ef4c4`）、Python 3.10.16，依 CI 的方式安裝（`uv pip install -e ./edge_core[vision]`）

錄製指令（在基準環境的 venv 中執行；`-I` 避免載入目前目錄的模組）：

```bash
python -I tests/regression/record_baseline.py --commit 5422b1e --smart-workflow "0.1.0 (b5ef4c4)"
```

基準環境沒有這個目錄，錄製時把本目錄複製過去執行，再把產生的 `data/business_baseline.json` 複製回來。不要在新版環境重新錄製。
