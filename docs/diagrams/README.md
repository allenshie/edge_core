# edge_core 圖表總覽

這個目錄是 `edge_core` 圖表的單一來源。
設計順序是刻意排過的：先看專案邊界，再看啟動流程，接著看資料流與設定，最後才看模組依賴。

## Figma 匯出圖

以下 SVG 是從 Figma board 匯出的 runtime flow 視覺版，適合快速閱讀啟動、訂閱與 pipeline 的關係。
如果你只想先看 runtime 啟動鏈路，這張圖也可以直接對照下方第 2 節。

![edge_core runtime flow](assets/edge-core-runtime-flow.svg)

- SVG 原始檔：[`assets/edge-core-runtime-flow.svg`](assets/edge-core-runtime-flow.svg)
- 對應來源：Figma board 內的 `edge_core Runtime Flow`

## 圖表索引

| # | 圖表 | 說明 | 原始檔 |
|---|---|---|---|
| 1 | 專案邊界與角色 | 說明 `edge_core`、site repo、app backend 與外部系統之間的邊界 | [repository-overview.mmd](repository-overview.mmd) |
| 2 | Runtime 啟動與關閉 | 說明 launcher 如何交給 `edge.main`、workflow runner 與 runtime services | [runtime-bootstrap.mmd](runtime-bootstrap.mmd) |
| 3 | 核心 pipeline 與 context 流 | 說明 frame、`TaskContext` resource 與輸出如何在 pipeline 中流動 | [edge-core-pipeline.mmd](edge-core-pipeline.mmd) |
| 4 | 設定與擴充點 | 說明哪些設定會影響 runtime 行為，以及 site repo 可以替換哪些元件 | [configuration-and-extension-points.mmd](configuration-and-extension-points.mmd) |
| 5 | 模組依賴圖 | 說明內部套件與 runtime / pipeline / tasks / engines 之間的關係 | [module-dependency.mmd](module-dependency.mmd) |

<a id="1-project-boundary-and-roles"></a>
## 1. 專案邊界與角色

這張圖說明 `edge_core` 的範圍、site repo 提供的擴充內容，以及它會接觸到的外部系統。

- Rendered view：本節
- Source：[`repository-overview.mmd`](repository-overview.mmd)
- Figma SVG：[`assets/edge-core-project-boundary.svg`](assets/edge-core-project-boundary.svg)

![edge_core project boundary](assets/edge-core-project-boundary.svg)

```mermaid
flowchart LR
    subgraph siteRepo["Site Repo"]
        siteEntry["site entrypoint"]
        siteConfig["schedules / configs / weights"]
        siteModels["custom models"]
    end

    subgraph edgeCore["edge_core"]
        launcher["edge_core/main.py"]
        edgeMain["edge.main"]
        config["edge.config"]
        messaging["edge.messaging"]
        runtime["edge.runtime"]
        pipeline["edge.pipeline"]
    end

    subgraph appBackend["App Backend"]
        routeApi["phase / matching / events routes"]
    end

    subgraph externalSystems["External Systems"]
        camera["RTSP / file / camera"]
        streamSink["stream sink"]
        monitor["monitoring endpoint"]
    end

    siteEntry --> launcher
    siteConfig --> config
    siteModels --> pipeline
    launcher --> edgeMain
    edgeMain --> config
    edgeMain --> messaging
    edgeMain --> runtime
    edgeMain --> pipeline
    camera --> pipeline
    pipeline --> streamSink
    runtime -.-> routeApi
    messaging -.-> routeApi
    runtime -.-> monitor
```

<a id="2-runtime-bootstrap-and-shutdown"></a>
## 2. Runtime 啟動與關閉

這張圖說明 `edge_core/main.py` 如何進入 `edge.main`，以及 runtime 在啟動、訂閱、執行與關閉時的順序。

- Rendered view：本節
- Source：[`runtime-bootstrap.mmd`](runtime-bootstrap.mmd)
- Figma SVG：[`assets/edge-core-runtime-flow.svg`](assets/edge-core-runtime-flow.svg)

```mermaid
sequenceDiagram
    participant User
    participant EdgeCoreLauncher
    participant EdgeMain
    participant MessagingClient
    participant StartSubscriber
    participant HealthServer
    participant WorkflowRunner
    participant EdgePipeline

    User->>EdgeCoreLauncher: start service
    EdgeCoreLauncher->>EdgeMain: call main()
    EdgeMain->>MessagingClient: build client
    EdgeMain->>StartSubscriber: scan enabled inbound routes
    EdgeMain->>HealthServer: start health server
    EdgeMain->>WorkflowRunner: build runner
    WorkflowRunner->>EdgePipeline: run startup task
    WorkflowRunner->>EdgePipeline: execute scheduler loop
    WorkflowRunner-->>EdgeMain: stop
    EdgeMain->>MessagingClient: close
    EdgeMain->>HealthServer: stop
```

<a id="3-core-pipeline-and-context-flow"></a>
## 3. 核心 pipeline 與 context 流

這張圖說明 frame 從取流、推理到串流與事件發布的主路徑，同時標出 `edge_mode` 與 `matching_result_snapshot` 是怎麼進入 `TaskContext` 的。

- Rendered view：本節
- Source：[`edge-core-pipeline.mmd`](edge-core-pipeline.mmd)
- Figma SVG：[`assets/edge-core-pipeline-context.svg`](assets/edge-core-pipeline-context.svg)

![edge_core pipeline context flow](assets/edge-core-pipeline-context.svg)

```mermaid
flowchart LR
    subgraph inputSources["Input Sources"]
        source["RTSP / File / Camera"]
    end

    subgraph runtimeStore["TaskContext Resources"]
        edgeMode["edge_mode"]
        matchingSnapshot["matching_result_snapshot"]
        frameMeta["frame_meta"]
    end

    subgraph pipeline["Pipeline"]
        ingestion["IngestionTask"]
        inference["InferenceTask"]
        streaming["StreamingTask"]
        publish["PublishResultTask"]
    end

    subgraph appBackend["App Backend"]
        inbound["phase / matching updates"]
    end

    subgraph outputs["Outputs"]
        streamOut["visual stream"]
        eventOut["events"]
    end

    source --> ingestion
    ingestion --> frameMeta
    frameMeta --> inference
    inference --> streaming
    inference --> publish
    inbound -.-> edgeMode
    inbound -.-> matchingSnapshot
    edgeMode --> inference
    edgeMode --> streaming
    matchingSnapshot --> streaming
    streaming --> streamOut
    publish --> eventOut
```

<a id="4-configuration-and-extension-points"></a>
## 4. 設定與擴充點

這張圖說明哪些設定控制 route wiring，哪些 site repo 元件可以替換或擴充 `edge_core` 的預設行為。

- Rendered view：本節
- Source：[`configuration-and-extension-points.mmd`](configuration-and-extension-points.mmd)
- Figma SVG：[`assets/edge-core-configuration-extensions.svg`](assets/edge-core-configuration-extensions.svg)

![edge_core configuration and extensions](assets/edge-core-configuration-extensions.svg)

```mermaid
flowchart LR
    subgraph siteRepo["Site Repo"]
        schedules["schedule.json"]
        modelConfigs["configs/models.yaml"]
        customModels["custom model classes"]
        customInference["custom inference engine"]
        customPublish["custom publish engine"]
        customStreaming["custom streaming engine"]
    end

    subgraph configuration["Configuration"]
        sharedBackend["shared inbound backend"]
        routeConfig["phase / matching / events routes"]
        engineOverrides["engine class overrides"]
    end

    subgraph edgeCore["edge_core runtime"]
        messagingProvider["MessagingClientProvider"]
        subscriber["start_messaging_subscriber"]
        inferenceTask["InferenceTask"]
        streamingTask["StreamingTask"]
        publishTask["PublishResultTask"]
        taskContext["TaskContext"]
    end

    schedules --> customInference
    modelConfigs --> customModels
    customModels --> inferenceTask
    customInference --> inferenceTask
    customPublish --> publishTask
    customStreaming --> streamingTask

    sharedBackend --> messagingProvider
    routeConfig --> subscriber
    engineOverrides --> inferenceTask
    engineOverrides --> streamingTask
    engineOverrides --> publishTask
    messagingProvider --> taskContext
    subscriber --> taskContext
    taskContext --> inferenceTask
    taskContext --> streamingTask
    taskContext --> publishTask
```

<a id="5-module-dependency-map"></a>
## 5. 模組依賴圖

這張圖說明 `edge_core` 內部模組之間的主要依賴關係，適合維護者快速定位 runtime、pipeline、tasks 與 engines 的連線方式。

- Rendered view：本節
- Source：[`module-dependency.mmd`](module-dependency.mmd)
- Figma SVG：[`assets/edge-core-module-dependencies.svg`](assets/edge-core-module-dependencies.svg)

![edge_core module dependencies](assets/edge-core-module-dependencies.svg)

```mermaid
graph LR
    Compat[edge_core/main.py]
    Main[edge.main]
    Config[edge.config]
    Messaging[edge.messaging]
    Runtime[edge.runtime]
    MessagingRuntime[messaging_runtime]
    HealthRuntime[health_runtime]
    PipelineSummary[pipeline_summary]
    ShutdownSummary[shutdown_summary]
    TaskHealth[task_health]
    Pipeline[pipeline]
    Tasks[edge.pipeline.tasks]
    Ingest[ingestion]
    Infer[inference]
    Stream[streaming]
    Publish[publish]
    Models[model helpers]
    Engines[streaming engines]
    Schema[edge.schema]

    Compat --> Main
    Main --> Config
    Main --> Messaging
    Main --> Runtime
    Main --> Pipeline
    Runtime --> MessagingRuntime
    Runtime --> HealthRuntime
    Runtime --> PipelineSummary
    Runtime --> ShutdownSummary
    Runtime --> TaskHealth
    Runtime --> Messaging
    Runtime --> Schema
    Pipeline --> Tasks
    Tasks --> Ingest
    Tasks --> Infer
    Tasks --> Stream
    Tasks --> Publish
    Infer --> Models
    Infer --> Schema
    Stream --> Engines
    Stream --> Messaging
    Stream --> Schema
    Publish --> Messaging
```

## 備註

- 這份 README 是圖表入口，不是圖表內容的唯一來源。
- `.mmd` 檔維持原始 Mermaid source，SVG 則提供更精美的視覺預覽。
- 新圖表請先加到這個目錄，再更新 README 索引，這樣入口就能維持穩定。
