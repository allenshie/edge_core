"""Inference task delegating to pluggable engines."""
from __future__ import annotations

import importlib
import os
import time
from datetime import datetime, timezone
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.runtime.rate_meter import RateMeter
from edge.runtime.task_health import TaskHealthReporter
from edge.api.mode_server import MODE_RESOURCE
from edge.schema import FrameMeta, StageStats

from .engine import BaseInferenceEngine, DefaultInferenceEngine


class InferenceTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-inference"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine = self._load_engine(context)
        self._stats = StageStats(task_name="infer")
        self._infer_rate = RateMeter()
        self._infer_latency = self._infer_rate
        self._last_detection_count = 0
        self._health = TaskHealthReporter(self._stats)

    def run(self, context: TaskContext) -> TaskResult:
        runtime = self._frame_runtime(context)
        frame = context.get_resource("decoded_frame")
        frame_meta = runtime.frame_meta
        if runtime.is_new_frame is False:
            return self._handle_stale_frame(context, runtime, frame_meta)
        return self._process_inference(context, runtime, frame, frame_meta)

    def _handle_stale_frame(
        self,
        context: TaskContext,
        runtime,
        frame_meta: FrameMeta | None,
    ) -> TaskResult:
        summary_fields = {
            "detections": self._last_detection_count,
            "skipped": True,
            "reason": "stale_frame",
            "skip_reason": "stale_frame",
        }
        return self._report_skip(
            context,
            stage="infer",
            frame_meta=frame_meta,
            note=f"skipped=stale_frame detections={self._last_detection_count}",
            reason="stale_frame",
            extra_fields=summary_fields,
            report_interval_seconds=runtime.report_interval_seconds,
            rate_meter=self._infer_rate,
            rate_prefix="infer",
            skipped_resources={
                "inference_models_run": [],
                "inference_models_reuse": [],
                "inference_skipped": True,
                "inference_skip_reason": "stale_frame",
            },
            payload={"detections": context.get_resource("inference_output") or [], "skipped": True},
        )

    def _process_inference(
        self,
        context: TaskContext,
        runtime,
        frame,
        frame_meta: FrameMeta | None,
    ) -> TaskResult:
        start = time.perf_counter()
        phase = self._resolve_phase(context)
        camera_id = context.config.camera.camera_id if context and context.config and context.config.camera else "unknown"
        outcome = self._engine.process(
            frame,
            phase=phase,
            metadata={"phase": phase, "camera_id": camera_id},
        )
        detections = outcome.detections
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        actual_inference = bool(outcome.models_run)
        if actual_inference:
            self._infer_rate.mark(frame_seq=frame_meta.frame_seq if isinstance(frame_meta, FrameMeta) else None)
        context.set_resource("inference_output", detections)
        context.set_resource("inference_models_run", list(outcome.models_run))
        context.set_resource("inference_models_reuse", list(outcome.models_reuse))
        context.set_resource("inference_skipped", False)
        context.set_resource("inference_skip_reason", None)
        self._last_detection_count = len(detections)
        self._record_success(
            self._stats,
            frame_meta,
            latency_ms=elapsed_ms if actual_inference else None,
            worker_alive=True,
            success_ts=datetime.now(timezone.utc),
        )
        model_cfg = getattr(self._engine, "_model_config", None)
        self._health.report_inference(
            context,
            frame_meta=frame_meta,
            detections_count=len(detections),
            model_config=model_cfg,
            infer_rate_meter=self._infer_rate,
            report_interval_seconds=runtime.report_interval_seconds,
            stale_threshold_seconds=runtime.stale_threshold_seconds,
        )
        return self._build_task_result({"detections": detections}, frame_meta)

    def _resolve_phase(self, context: TaskContext) -> str:
        phase = context.get_resource(MODE_RESOURCE)
        if not phase:
            phase = (
                os.environ.get("EDGE_MODE_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT_PHASE")
                or "working_stage_1"
            )
        return str(phase)

    def snapshot_health(self, context: TaskContext | None = None) -> dict:
        model_cfg = getattr(self._engine, "_model_config", None)
        return self._health.snapshot_inference(
            frame_meta=None,
            detections_count=self._last_detection_count,
            model_config=model_cfg,
            infer_rate_meter=self._infer_rate,
        )

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        return self.snapshot_health(context)

    def _load_engine(self, context: TaskContext | None) -> BaseInferenceEngine:
        engine_path = getattr(context.config, "inference_engine_class", None) if context else None
        if not engine_path:
            return DefaultInferenceEngine(context=context)
        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

    def _import_engine(self, path: str) -> Type[BaseInferenceEngine]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 Inference Engine：{path}")
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if engine_cls is None or not issubclass(engine_cls, BaseInferenceEngine):
            raise TaskError(f"{class_name} 必須繼承 BaseInferenceEngine")
        return engine_cls

    def close(self, context: TaskContext) -> list[dict]:
        _ = context
        close_fn = getattr(self._engine, "close", None)
        if callable(close_fn):
            result = close_fn()
            if isinstance(result, list):
                return result
            if result is not None:
                return [result]
        return []
