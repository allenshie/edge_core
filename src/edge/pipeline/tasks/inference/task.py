"""Inference task delegating to pluggable engines."""
from __future__ import annotations

import importlib
import os
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.schema import FrameMeta

from .engine import BaseInferenceEngine, DefaultInferenceEngine
from .errors import raise_if_cuda_unrecoverable


class InferenceTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-inference"
    health_node = "infer"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine = self._load_engine(context)

    def run(self, context: TaskContext) -> TaskResult:
        runtime = self._frame_runtime(context)
        frame = context.get_resource("decoded_frame")
        frame_meta = runtime.frame_meta
        if runtime.is_new_frame is False:
            return self._handle_stale_frame(context, runtime, frame_meta)
        return self._process_inference(context, runtime, frame, frame_meta)

    def _handle_stale_frame(self, context: TaskContext, runtime, frame_meta: FrameMeta | None) -> TaskResult:
        return self._skip_result(
            context,
            frame_meta=frame_meta,
            reason="stale_frame",
            skipped_resources={
                "inference_models_run": [],
                "inference_models_reuse": [],
                "inference_skipped": True,
                "inference_skip_reason": "stale_frame",
            },
            payload={"detections": context.get_resource("inference_output") or [], "skipped": True},
        )

    def _process_inference(self, context: TaskContext, runtime, frame, frame_meta: FrameMeta | None) -> TaskResult:
        phase = self._resolve_phase(context)
        camera_id = context.config.camera.camera_id if context and context.config and context.config.camera else "unknown"
        try:
            outcome = self._engine.process(
                frame,
                phase=phase,
                metadata={"phase": phase, "camera_id": camera_id},
            )
        except Exception as exc:
            raise_if_cuda_unrecoverable(exc)
            raise
        detections = outcome.detections
        context.set_resource("inference_output", detections)
        context.set_resource("inference_models_run", list(outcome.models_run))
        context.set_resource("inference_models_reuse", list(outcome.models_reuse))
        context.set_resource("inference_skipped", False)
        context.set_resource("inference_skip_reason", None)
        result, reason, message = self._engine_result(outcome)
        if result == "success":
            self.set_metric("detections", len(detections))
        return self._build_task_result(
            {"detections": detections}, frame_meta, status=result, reason=reason, message=message
        )

    def warmup(self) -> None:
        """啟動時暖機（只在啟用健康回報時由 EdgePipeline.warmup() 呼叫）。"""
        warmup = getattr(self._engine, "warmup", None)
        if callable(warmup):
            warmup()

    @property
    def engine(self) -> BaseInferenceEngine:
        return self._engine

    def current_phase(self, context: TaskContext) -> str:
        """目前的時段，解析方式和推理時相同；供健康架構的 enabled 判斷使用。"""
        return self._resolve_phase(context)

    def _resolve_phase(self, context: TaskContext) -> str:
        phase_resource_name = self._resolve_phase_resource_name(context)
        phase = context.get_resource(phase_resource_name)
        if not phase:
            phase = (
                os.environ.get("EDGE_MODE_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT_PHASE")
                or "working_stage_1"
            )
        return str(phase)

    def _resolve_phase_resource_name(self, context: TaskContext) -> str:
        phase_cfg = getattr(context.config, "phase_messaging", None)
        resource_name = getattr(phase_cfg, "resource_name", None)
        return str(resource_name or "edge_mode")

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
