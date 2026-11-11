"""D1 本機驗證用的故障注入類別（spec stage-d1 2.4）。

只透過 `*_ENGINE_CLASS`／`INGESTION_ENGINE_CLASS` 替換類別使用，正式程式碼沒有任何測試開關。
旗標檔放在 `D1_FAULT_DIR`（預設 `/tmp/d1cam`），檔案存在時才製造故障。
執行時要把 `edge_core/tests` 加進 `PYTHONPATH`，類別路徑寫成 `fault_injection.<模組>:<類別>`。
"""
