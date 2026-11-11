"""Edge pipeline composition and workflow hooks."""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable, List

from smart_workflow import BaseTask, TaskContext, TaskResult

from edge.pipeline.tasks.inference import InferenceTask
from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.publish import PublishResultTask
from edge.pipeline.tasks.streaming import StreamingTask
from edge.runtime.shutdown_summary import (
    append_shutdown_records,
    cleanup_record,
    normalize_cleanup_records,
)


def _health_enabled(context: TaskContext) -> bool:
    return bool(getattr(getattr(context, "health", None), "enabled", False))


class EdgePipeline:
    """Sequential pipeline that reuses instantiated tasks across loop runs."""

    def __init__(self, nodes: Iterable[BaseTask]) -> None:
        self._nodes: List[BaseTask] = list(nodes)
        self._closed = False

    def warmup(self, context: TaskContext) -> None:
        context.logger.info("edge pipeline initialized with %d nodes", len(self._nodes))
        # 只有啟用健康回報時才暖機（EDGE_HEALTH_ENABLED=0 時行為和導入前相同）。
        if not _health_enabled(context):
            return
        for node in self._nodes:
            warmup = getattr(node, "warmup", None)
            if callable(warmup):
                warmup()

    def bind_background(self, context: TaskContext) -> None:
        """把 Engine 的背景執行緒狀態交給健康回報（ingest-worker、stream-output）。"""
        if not _health_enabled(context):
            return
        ingestion = self.find_node(IngestionTask)
        engine = getattr(ingestion, "engine", None)
        if engine is not None and callable(getattr(engine, "status", None)):
            context.health.bind_background("ingest-worker", engine.status, stuck_exit_s=engine.stuck_exit_seconds())
        streaming = self.find_node(StreamingTask)
        engine = getattr(streaming, "engine", None) if getattr(streaming, "enabled", False) else None
        if engine is not None and callable(getattr(engine, "status", None)):
            context.health.bind_background("stream-output", engine.status)

    def execute(self, context: TaskContext) -> None:
        for node in self._nodes:
            node.execute(context)

    def find_node(self, task_type: type) -> BaseTask | None:
        """回傳第一個屬於 task_type 的節點；健康架構用來取得 Engine。"""
        for node in self._nodes:
            if isinstance(node, task_type):
                return node
        return None

    def begin_shutdown(self) -> None:
        for node in self._nodes:
            begin_shutdown = getattr(node, "begin_shutdown", None)
            if callable(begin_shutdown):
                try:
                    begin_shutdown()
                except Exception:  # noqa: BLE001
                    continue

    def close(self, context: TaskContext) -> list[dict[str, Any]]:
        if self._closed:
            return []
        records: list[dict[str, Any]] = []
        self.begin_shutdown()
        for node in reversed(self._nodes):
            try:
                result = node.close(context)
                node_name = getattr(node, "name", node.__class__.__name__)
                records.extend(
                    normalize_cleanup_records(
                        result,
                        fallback_item=node_name,
                        fallback_type="task",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                node_name = getattr(node, "name", node.__class__.__name__)
                context.logger.exception("failed to close node task: %s", node_name)
                records.append(
                    cleanup_record(
                        item=node_name,
                        type="task",
                        state="failed",
                        ok=False,
                        alive_before=True,
                        alive_after=True,
                        detail="node close raised exception",
                        error=str(exc),
                    )
                )
        append_shutdown_records(context, records)
        self._closed = True
        return records


class InitPipelineTask(BaseTask):
    """Bootstrap the pipeline and store it inside TaskContext resources."""

    name = "edge-pipeline-init"

    def run(self, context: TaskContext) -> TaskResult:
        factories: List[Callable[[TaskContext], BaseTask]] = [
            IngestionTask,
            InferenceTask,
            StreamingTask,
            PublishResultTask,
        ]
        nodes = [factory(context) for factory in factories]
        pipeline = EdgePipeline(nodes)
        pipeline.bind_background(context)
        pipeline.warmup(context)
        context.set_resource("edge_pipeline", pipeline)
        return TaskResult()


class PipelineScheduler(BaseTask):
    """Loop task that keeps executing the prepared pipeline."""

    name = "edge-pipeline-scheduler"

    def run(self, context: TaskContext) -> TaskResult:
        pipeline: EdgePipeline = context.require_resource("edge_pipeline")
        target_interval = self._get_target_interval(context)
        start_time = time.monotonic()
        pipeline.execute(context)

        elapsed = time.monotonic() - start_time
        sleep_time = max(0.001, target_interval - elapsed) if target_interval else 0.0

        # 交由 WorkflowRunner 統一 sleep；這裡只回傳下一輪建議等待時間。
        return TaskResult(payload={"sleep": sleep_time})

    def _get_target_interval(self, context: TaskContext) -> float | None:
        interval = getattr(context.config, "poll_interval", 0.0)
        return interval if interval and interval > 0 else None

    def close(self, context: TaskContext) -> None:
        pipeline: EdgePipeline | None = context.get_resource("edge_pipeline")
        if pipeline is None:
            return
        begin_shutdown = getattr(pipeline, "begin_shutdown", None)
        if callable(begin_shutdown):
            begin_shutdown()
        pipeline.close(context)
