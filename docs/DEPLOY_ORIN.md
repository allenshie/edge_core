# Orin 部署指南（ARM / Jetson）

本文件說明在 NVIDIA Jetson Orin 上部署 `edge_core` 的建議方式。

## 目標與原則

- 使用 Jetson/Orin 已驗證可用的 GPU 套件（特別是 `torch`）。
- 避免安裝流程覆蓋本機已編譯或已預裝的 GPU 相關套件。
- 專案程式本體仍以 `pip install -e` 安裝，保留可維護性。

## 前置條件

1. 已安裝對應 JetPack 與 CUDA 環境。
2. 已可在本機 Python 中成功載入 GPU 版 `torch`。
3. 若環境已預裝 OpenCV/FFmpeg，也建議沿用系統版本。

建議先驗證：

```bash
python - <<'PY'
import torch
print("torch:", torch.__version__)
print("cuda:", torch.cuda.is_available())
PY
```

## 為何不直接安裝 `.[vision]`

`.[vision]` 會包含 `ultralytics`、`opencv-python-headless` 等依賴。  
在 Orin/ARM 環境，這些依賴可能：

- 找不到合適 wheel 而改為原始碼編譯；
- 或覆蓋你已準備好的系統套件版本。

因此 Orin 建議使用「先備系統套件 + 專案無依賴安裝」模式。

## 建議安裝流程（Orin）

```bash
# 1) 建議使用 venv 隔離；若 torch 安裝在系統層，可考慮沿用 system site-packages
python3 -m venv .venv --system-site-packages
source .venv/bin/activate

# 2) 先升級建置工具鏈，避免舊版 pip/setuptools 無法正確處理 pyproject
python -m pip install --upgrade pip setuptools wheel

# 3) 先確認 torch 可用（需為你在 Orin 準備好的版本）
python -c "import torch; print(torch.cuda.is_available())"

# 4) 安裝 edge_core 本體，但不自動安裝 dependencies
pip install -e . --no-deps

# 5) 依需求手動安裝其餘套件（避免覆蓋 torch / opencv）
#    下面為示例，請依你的 Orin 驗證版本調整
pip install "numpy>=1.26,<2" "lap>=0.4" "paho-mqtt>=1.6.1"
pip install "ultralytics==8.3.236"
pip install "smart-workflow @ git+https://github.com/allenshie/smart-workflow.git@346a65f87394d0fe39b8761734447940b901ab62"
pip install "smart-messaging-core==0.1.1"
```

## 版本相容建議

- `numpy` 建議維持 `<2`（避免與既有 PyTorch 編譯環境不相容）。
- `ultralytics` 建議固定版本，不要放任自動升級。
- 若 OpenCV 已由系統提供，避免再裝 `opencv-python-headless` 覆蓋。

## 啟動前檢查

```bash
python - <<'PY'
import numpy, torch
print("numpy:", numpy.__version__)
print("torch:", torch.__version__, "cuda:", torch.cuda.is_available())
PY
```

再啟動服務：

```bash
python main.py
```

## 以 systemd 常駐（健康回報）

launcher repo（例如 `smart_intersection_safety_edge`）的 `deploy/systemd/` 提供 `edge@.service`、`health-disabled.conf` 與 `env/cam01.env.example`。Edge 啟動完成（暖機後）送 `READY=1`，之後每秒送 `WATCHDOG=1`；判斷為無法恢復時以代碼 70 結束，由 `Restart=on-failure` 重啟。

### 安裝與啟用

```bash
# 1) 每台相機一份 env（只放這台的設定；帳密另外填入，不要提交到 repo）
sudo install -d -m 0750 /etc/smart-edge
sudo install -m 0640 deploy/systemd/env/cam01.env.example /etc/smart-edge/cam01.env
sudoedit /etc/smart-edge/cam01.env

# 2) 安裝 unit（ExecStart、WorkingDirectory 預設 /opt/smart_intersection_safety_edge，路徑不同時先修改）
sudo install -m 0644 deploy/systemd/edge@.service /etc/systemd/system/edge@.service
sudo systemctl daemon-reload
sudo systemctl enable --now edge@cam01
```

`ExecStart` 直接執行 venv 裡的 python（不經過 shell），Python 才是主程序，`NotifyAccess=main` 才會收到通知。`EnvironmentFile` 的值優先；專案目錄的 `.env` 只補上沒有設定的變數。

### 查看狀態與 log

```bash
systemctl status edge@cam01
journalctl -u edge@cam01 -f
journalctl -u edge@cam01 --since "1 hour ago" | grep -E "CRITICAL|無法恢復|健康"
curl -s http://127.0.0.1:18081/status    # 有設定 EDGE_HEALTH_STATUS_PORT 時
```

### 對時檢查

健康判斷都用 monotonic 時鐘，但 log 與 `/status` 的時間戳記是系統時間，跨主機比對前請確認已對時：

```bash
timedatectl status    # 確認 System clock synchronized: yes
```

### Port 分配

| 相機 | `EDGE_HEALTH_STATUS_PORT` |
|---|---|
| cam01 | 18081 |
| cam02 | 18082 |
| cam03 | 18083 |
| cam04 | 18084 |
| cam05 | 18085 |
| cam06 | 18086 |

`StatusServer` 沒有驗證，只能開放內網：Orin 上設 `EDGE_HEALTH_STATUS_HOST=0.0.0.0` 時，請用防火牆限制只有內網或監控主機可以連線；不需要從其他主機查詢時維持預設 `127.0.0.1`。

### 數值的關係

- `WatchdogSec=10`：回報執行緒每秒送一次 `WATCHDOG=1`，10 秒沒有收到才視為卡住。
- `TimeoutStartSec=300`：包含模型載入與暖機，和 k8s startupProbe 的總時限（`failureThreshold 150 × periodSeconds 2`）相同。
- `StartLimitIntervalSec=300`、`StartLimitBurst=5`：5 分鐘內重啟超過 5 次就停止重啟，請查看 log。

### 退回方式

- **觀察模式**：env 設 `EDGE_HEALTH_EXIT_ON_FAILURE=0`，照常判斷並寫 CRITICAL log，但不結束程式。unit 不需要修改。
- **完全停用**：env 設 `EDGE_HEALTH_ENABLED=0`，並安裝 drop-in。停用時不會送 `READY=1` 和 `WATCHDOG=1`，沒有 drop-in 時 systemd 會在 `TimeoutStartSec` 後結束程式。

```bash
sudo install -d /etc/systemd/system/edge@cam01.service.d
sudo install -m 0644 deploy/systemd/health-disabled.conf /etc/systemd/system/edge@cam01.service.d/health-disabled.conf
sudo systemctl daemon-reload && sudo systemctl restart edge@cam01
```

恢復時刪除 drop-in、把 `EDGE_HEALTH_ENABLED` 改回 `1`，再 `daemon-reload` 與 `restart`。

## 常見問題

- 問題：`A module that was compiled using NumPy 1.x cannot be run in NumPy 2.x`  
  處理：將 `numpy` 降回 `<2`，並避免被其他套件升級。

- 問題：安裝過程把既有 GPU 套件版本改掉  
  處理：改用 `pip install -e . --no-deps`，再手動安裝其餘依賴。

- 問題：`pip install -e .` 出現 `UNKNOWN-0.0.0` 或 `build_editable` 相關錯誤  
  處理：先升級 `pip setuptools wheel`，再重新安裝。
