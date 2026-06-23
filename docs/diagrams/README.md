# Diagrams

This directory is the source of truth for `edge_core` diagrams.

## What is here

- A renderable Markdown page for each major diagram
- Raw `.mmd` source files for reuse and review

## Diagram Index

### 1. Repository Layout

Shows the `edge_core` package boundary, its main runtime entry, and the major internal modules.

- Rendered view: this section
- Source: [repository-overview.mmd](repository-overview.mmd)

```mermaid
graph TD
    Root[edge_core]
    Launcher[main.py]
    EdgePkg[src edge]
    EdgeMain[edge.main]
    Config[edge.config]
    Schema[edge.schema]
    Messaging[edge.messaging]
    Runtime[edge.runtime]
    Pipeline[edge.pipeline]
    API[edge.api]
    Tasks[edge.pipeline.tasks]
    Ingest[ingestion]
    Infer[inference]
    Stream[streaming]
    Publish[publish]

    Root --> Launcher
    Root --> EdgePkg
    Launcher --> EdgeMain
    EdgePkg --> Config
    EdgePkg --> Schema
    EdgePkg --> Messaging
    EdgePkg --> Runtime
    EdgePkg --> Pipeline
    EdgePkg --> API
    Pipeline --> Tasks
    Tasks --> Ingest
    Tasks --> Infer
    Tasks --> Stream
    Tasks --> Publish
```

### 2. Runtime Bootstrap

Shows how `edge_core/main.py` resolves configuration, injects paths, and hands off to `edge.main`.

- Rendered view: this section
- Source: [runtime-bootstrap.mmd](runtime-bootstrap.mmd)

```mermaid
sequenceDiagram
    participant User
    participant Launcher as main.py
    participant EnvLoader as app.config
    participant AppRuntime as app.runtime
    participant EdgeMain as edge.main
    participant Workflow as WorkflowRunner

    User->>Launcher: start service
    Launcher->>EnvLoader: load_app_environment
    EnvLoader-->>Launcher: resolved paths
    Launcher->>AppRuntime: run
    AppRuntime->>AppRuntime: inject sys.path
    AppRuntime->>AppRuntime: apply environment defaults
    AppRuntime->>EdgeMain: call main
    EdgeMain->>Workflow: build context and workflow
    Workflow->>Workflow: execute startup and loop
```

### 3. Edge Pipeline

Shows the main frame-processing path from ingestion to publish, plus the inbound messaging subscriber that writes phase and matching snapshots into `TaskContext`.

- Rendered view: this section
- Source: [edge-core-pipeline.mmd](edge-core-pipeline.mmd)

```mermaid
flowchart LR
    Source[RTSP / MP4 / Camera]
    Ingest[IngestionTask]
    FrameMeta[decoded_frame and frame_meta]
    Infer[InferenceTask]
    Detect[EdgeDetection list]
    Stream[StreamingTask]
    Publish[PublishResultTask]
    Output[stream and events]
    Subscriber[Messaging subscriber]
    Phase[phase resource<br/>edge_mode]
    Matching[matching_result_snapshot resource]

    Source --> Ingest
    Ingest --> FrameMeta
    FrameMeta --> Infer
    Infer --> Detect
    Detect --> Stream
    Detect --> Publish
    Subscriber --> Phase
    Subscriber --> Matching
    Phase --> Infer
    Phase --> Stream
    Matching --> Stream
    Stream --> Output
    Publish --> Output
```

### 4. Core Module Dependencies

Shows the main internal dependencies and how the runtime, pipeline, tasks, and engines connect.

- Rendered view: this section
- Source: [module-dependency.mmd](module-dependency.mmd)

```mermaid
graph LR
    Main[edge.main]
    Config[edge.config]
    Messaging[edge.messaging]
    Runtime[edge.runtime]
    Pipeline[edge.pipeline]
    Tasks[edge.pipeline.tasks]
    Ingest[ingestion]
    Infer[inference]
    Stream[streaming]
    Publish[publish]
    Models[model helpers]
    Engines[streaming engines]
    Schema[edge.schema]

    Main --> Config
    Main --> Messaging
    Main --> Runtime
    Main --> Pipeline
    Pipeline --> Tasks
    Tasks --> Ingest
    Tasks --> Infer
    Tasks --> Stream
    Tasks --> Publish
    Runtime --> Messaging
    Runtime --> Schema
    Infer --> Models
    Stream --> Engines
    Stream --> Messaging
    Stream --> Schema
    Publish --> Messaging
```

## Notes

- Keep the Markdown page and the `.mmd` source files in sync.
- Add new diagrams here first so the README only needs one stable link.
