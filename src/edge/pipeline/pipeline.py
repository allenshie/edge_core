"""Edge pipeline composition and workflow hooks."""
from __future__ import annotations

import logging
import time
from typing import Callable, Iterable, List

from smart_workflow import BaseTask, TaskContext, TaskResult

from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.inference import InferenceTask
from edge.pipeline.tasks.publish import PublishResultTask
from edge.pipeline.tasks.streaming import StreamingTask

LOGGER = logging.getLogger(__name__)


class EdgePipeline:
    """Sequential pipeline that reuses instantiated tasks across loop runs."""

    def __init__(self, nodes: Iterable[BaseTask]) -> None:
        self._nodes: List[BaseTask] = list(nodes)

    def warmup(self, context: TaskContext) -> None:
        context.logger.info("edge pipeline initialized with %d nodes", len(self._nodes))

    def execute(self, context: TaskContext) -> None:
        for node in self._nodes:
            node.execute(context)

    def close(self, context: TaskContext) -> None:
        for node in reversed(self._nodes):
            try:
                node.close(context)
            except Exception:  # noqa: BLE001
                node_name = getattr(node, "name", node.__class__.__name__)
                context.logger.exception("failed to close node task: %s", node_name)


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

    def run(self, context: TaskContext) -> TaskResult:
        pipeline: EdgePipeline = context.require_resource("edge_pipeline")
        target_interval = self._get_target_interval(context)
        start_time = time.monotonic()

        context.monitor.heartbeat(phase="edge_pipeline")
        pipeline.execute(context)

        elapsed = time.monotonic() - start_time
        sleep_time = max(0.0, target_interval - elapsed) if target_interval else 0.0
        LOGGER.debug(
            "pipeline finished in %.4fs (target %.4fs), sleep=%.4fs",
            elapsed,
            (target_interval or 0.0),
            sleep_time,
        )
        if sleep_time > 0:
            time.sleep(sleep_time)
        return TaskResult(payload={"sleep": sleep_time})

    def _get_target_interval(self, context: TaskContext) -> float | None:
        fps_value = self._resolve_fps(context)
        if fps_value and fps_value > 0:
            return 1.0 / fps_value
        interval = getattr(context.config, "poll_interval", 0.0)
        return interval if interval and interval > 0 else None

    def _resolve_fps(self, context: TaskContext) -> float | None:
        ingestion_cfg = getattr(context.config, "ingestion", None)
        if ingestion_cfg:
            mode = getattr(ingestion_cfg, "mode", "rtsp") or "rtsp"
            mode = mode.strip().lower()
            if mode == "file":
                fps_value = getattr(ingestion_cfg.file, "fps", None)
                if fps_value and fps_value > 0:
                    return fps_value
                fallback = getattr(ingestion_cfg.rtsp, "fps", None)
                if fallback and fallback > 0:
                    return fallback
            elif mode == "camera":
                fps_value = getattr(ingestion_cfg.camera, "fps", None)
                if fps_value and fps_value > 0:
                    return fps_value
            else:
                fps_value = getattr(ingestion_cfg.rtsp, "fps", None)
                if fps_value and fps_value > 0:
                    return fps_value
        rtsp_cfg = getattr(context.config, "rtsp", None)
        if rtsp_cfg:
            fps_value = getattr(rtsp_cfg, "fps", None)
            if fps_value and fps_value > 0:
                return fps_value
        return None

    def close(self, context: TaskContext) -> None:
        pipeline: EdgePipeline | None = context.get_resource("edge_pipeline")
        if pipeline is None:
            return
        pipeline.close(context)
