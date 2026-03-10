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
# 1) 建議使用 venv 隔離
python3 -m venv .venv
source .venv/bin/activate

# 2) 先確認 torch 可用（需為你在 Orin 準備好的版本）
python -c "import torch; print(torch.cuda.is_available())"

# 3) 安裝 edge_core 本體，但不自動安裝 dependencies
pip install -e . --no-deps

# 4) 依需求手動安裝其餘套件（避免覆蓋 torch / opencv）
#    下面為示例，請依你的 Orin 驗證版本調整
pip install "numpy>=1.26,<2" "lap>=0.4" "paho-mqtt>=1.6.1"
pip install "ultralytics==8.3.236"
pip install "smart-workflow @ git+https://github.com/allenshie/smart-workflow.git@346a65f87394d0fe39b8761734447940b901ab62"
pip install "smart-messaging-core @ git+https://github.com/allenshie/smart_messaging_core.git@9dcfcf2bdc816eb4ef37910b00e33b9f96e88065"
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

## 常見問題

- 問題：`A module that was compiled using NumPy 1.x cannot be run in NumPy 2.x`  
  處理：將 `numpy` 降回 `<2`，並避免被其他套件升級。

- 問題：安裝過程把既有 GPU 套件版本改掉  
  處理：改用 `pip install -e . --no-deps`，再手動安裝其餘依賴。
