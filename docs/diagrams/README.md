# edge_core 圖表總覽

這個目錄同時保留 `.mmd` 原始檔與 HTML 版。HTML 版是主要閱讀入口，`.mmd` 保留可編輯來源。

設計順序刻意排過：先看專案邊界，再看啟動流程，接著看資料流與設定，最後看模組依賴。

## 圖表索引

| # | 圖表 | HTML | 原始檔 |
|---|---|---|---|
| 1 | 專案邊界與角色 | [repository-overview.html](html/repository-overview.html) | [repository-overview.mmd](repository-overview.mmd) |
| 2 | Runtime 啟動與關閉 | [runtime-bootstrap.html](html/runtime-bootstrap.html) | [runtime-bootstrap.mmd](runtime-bootstrap.mmd) |
| 3 | 核心 pipeline 與 context 流 | [edge-core-pipeline.html](html/edge-core-pipeline.html) | [edge-core-pipeline.mmd](edge-core-pipeline.mmd) |
| 4 | 設定與擴充點 | [configuration-and-extension-points.html](html/configuration-and-extension-points.html) | [configuration-and-extension-points.mmd](configuration-and-extension-points.mmd) |
| 5 | 模組依賴圖 | [module-dependency.html](html/module-dependency.html) | [module-dependency.mmd](module-dependency.mmd) |

## 備註

- HTML 版優先用來閱讀與檢視視覺結果。
- `.mmd` 仍是圖表的修改來源。
- 如果你要新增圖表，先加 `.mmd`，再同步補上 HTML 與這份索引。
