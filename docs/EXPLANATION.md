# Edge Core 設計理念與 DeepStream 關係

`edge_core` 的目標不是重現 DeepStream 的實作，而是保留其 pipeline 分工方式，將取流、推理、串流輸出、事件發布與狀態控制拆分為可組裝的 workflow / task。此設計使 runtime 能以 Python 為主要實作方式，在維持資料流責任切分的前提下，降低部署與擴充成本。

## 設計考量

DeepStream 以 NVIDIA 技術堆疊與相關插件生態為基礎，包含 CUDA、TensorRT、GStreamer 及對應擴充元件。實務上，模型通常需要先轉成可部署的格式或 engine，且客製 parser / plugin 的維護成本較高；此外，部署流程也較容易受到驅動版本、系統相依與硬體平台限制。

`edge_core` 因此採用以 workflow / task 為核心的架構，將可替換的功能集中在 Python 類別與設定檔中，讓使用端專案可以在不修改共用 runtime 的前提下調整行為。

## 對應關係

| 面向 | DeepStream | `edge_core` |
| --- | --- | --- |
| Pipeline 組裝 | 以 GStreamer element 與 plugin 組成串流管線 | 以 `TaskContext`、`WorkflowRunner` 與 Python tasks 組成 workflow |
| 模型部署 | 多半需轉成可部署的 engine / runtime 格式 | 可直接使用 Python inference engine 或使用端專案的 model 類別 |
| 擴充方式 | 常見為 C/C++ plugin、parser、element 擴充 | 以 Python 類別、engine、task、config 擴充 |
| 狀態傳遞 | 依賴 pipeline metadata 與 element 間流動 | 依賴 `TaskContext` resources 與 messaging subscriber |
| 部署依賴 | NVIDIA driver / CUDA / TensorRT / GStreamer plugin 生態 | 主要為 Python 專案與場域設定 |
| 適用情境 | 高吞吐、低延遲、標準化視覺分析管線 | 場域流程客製、開發迭代、可配置控制邏輯 |

## 設計原則

- 以 runtime 骨架為主，不將 domain 模型或場域規則寫入共用核心。
- 以設定與自訂類別替換 inference、publish、streaming 的實作。
- 讓 phase、health、streaming 與 messaging 彼此解耦，避免單一子系統狀態直接影響整體 runtime。

## 適用情境

- 需要較高的可維護性與可配置性
- 需要快速替換模型、排程或場域控制邏輯
- 需要獨立管理 health、readiness 與 streaming 行為

若要查閱 runtime 啟動、環境變數、health probe 或部署方式，請參考：

- [README](../README.md)
- [ENV.md](ENV.md)
- [HEALTH.md](HEALTH.md)
- [OPERATIONS.md](OPERATIONS.md)
