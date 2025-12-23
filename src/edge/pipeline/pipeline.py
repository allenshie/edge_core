"""Edge pipeline composition and workflow hooks."""
from __future__ import annotations

import logging
import time
from typing import Callable, Iterable, List

from smart_workflow import BaseTask, TaskContext, TaskResult

from edge.pipeline.tasks.ingestion import FileIngestionTask, RtspIngestionTask
from edge.pipeline.tasks.inference import InferenceTask
from edge.pipeline.tasks.publish import PublishResultTask

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


class InitPipelineTask(BaseTask):
    """Bootstrap the pipeline and store it inside TaskContext resources."""

    name = "edge-pipeline-init"

    def run(self, context: TaskContext) -> TaskResult:
        ingestion_factory, mode = self._select_ingestion_factory(context)
        factories: List[Callable[[TaskContext], BaseTask]] = [
            ingestion_factory,
            InferenceTask,
            PublishResultTask,
        ]
        context.logger.info("edge ingestion mode: %s", mode)
        nodes = [factory(context) for factory in factories]
        pipeline = EdgePipeline(nodes)
        pipeline.warmup(context)
        context.set_resource("edge_pipeline", pipeline)
        return TaskResult()

    def _select_ingestion_factory(self, context: TaskContext) -> tuple[Callable[[TaskContext], BaseTask], str]:
        ingestion_cfg = getattr(context.config, "ingestion", None)
        mode = (ingestion_cfg.mode if ingestion_cfg else "rtsp") if hasattr(ingestion_cfg, "mode") else "rtsp"
        mode = (mode or "rtsp").strip().lower()
        if mode == "file":
            return FileIngestionTask, mode
        return RtspIngestionTask, mode


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
