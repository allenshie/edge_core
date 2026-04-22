"""Edge pipeline composition and workflow hooks."""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable, List

from smart_workflow import BaseTask, TaskContext, TaskResult

from edge.runtime.pipeline_summary import build_pipeline_summary
from edge.runtime.shutdown_summary import append_shutdown_records, cleanup_record, normalize_cleanup_records
from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.inference import InferenceTask
from edge.pipeline.tasks.publish import PublishResultTask
from edge.pipeline.tasks.streaming import StreamingTask


class EdgePipeline:
    """Sequential pipeline that reuses instantiated tasks across loop runs."""

    def __init__(self, nodes: Iterable[BaseTask]) -> None:
        self._nodes: List[BaseTask] = list(nodes)
        self._closed = False

    def warmup(self, context: TaskContext) -> None:
        context.logger.info("edge pipeline initialized with %d nodes", len(self._nodes))

    def execute(self, context: TaskContext) -> None:
        for node in self._nodes:
            node.execute(context)

    def health_rows(self, context: TaskContext) -> list[dict]:
        rows: list[dict] = []
        for node in self._nodes:
            health_snapshot = getattr(node, "health_snapshot", None)
            if not callable(health_snapshot):
                continue
            snapshot = health_snapshot(context)
            if snapshot:
                rows.append(dict(snapshot))
        return rows

    def close(self, context: TaskContext) -> list[dict[str, Any]]:
        if self._closed:
            return []
        records: list[dict[str, Any]] = []
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
        pipeline.warmup(context)
        context.set_resource("edge_pipeline", pipeline)
        return TaskResult()


class PipelineScheduler(BaseTask):
    """Loop task that keeps executing the prepared pipeline."""

    name = "edge-pipeline-scheduler"

    def __init__(self) -> None:
        self._last_pipeline_summary_log_ts = 0.0

    def run(self, context: TaskContext) -> TaskResult:
        pipeline: EdgePipeline = context.require_resource("edge_pipeline")
        target_interval = self._get_target_interval(context)
        report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
        start_time = time.monotonic()

        context.monitor.heartbeat(phase="edge_pipeline")
        try:
            pipeline.execute(context)
        finally:
            self._emit_pipeline_summary(context, pipeline, report_interval_seconds=report_interval)

        elapsed = time.monotonic() - start_time
        sleep_time = max(0.001, target_interval - elapsed) if target_interval else 0.0

        # 交由 WorkflowRunner 統一 sleep；這裡只回傳下一輪建議等待時間。
        return TaskResult(payload={"sleep": sleep_time})

    def _get_target_interval(self, context: TaskContext) -> float | None:
        interval = getattr(context.config, "poll_interval", 0.0)
        return interval if interval and interval > 0 else None

    def _emit_pipeline_summary(self, context: TaskContext, pipeline: EdgePipeline, *, report_interval_seconds: float) -> None:
        now = time.monotonic()
        if report_interval_seconds > 0 and self._last_pipeline_summary_log_ts > 0:
            if (now - self._last_pipeline_summary_log_ts) < report_interval_seconds:
                return
        rows = pipeline.health_rows(context)
        if not rows:
            return
        summary = build_pipeline_summary(rows)
        context.logger.info(summary)
        self._last_pipeline_summary_log_ts = now

    def close(self, context: TaskContext) -> None:
        pipeline: EdgePipeline | None = context.get_resource("edge_pipeline")
        if pipeline is None:
            return
        pipeline.close(context)
