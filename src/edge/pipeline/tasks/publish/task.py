"""發布推理結果至整合端（委派至 engine）。"""
from __future__ import annotations

import importlib
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.schema import FrameMeta

from .capture_age import CaptureAgeWindow
from .engine import BasePublishEngine, MessagingPublishEngine


class PublishResultTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-publish"
    health_node = "publish"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._enabled = self._resolve_enabled(context)
        self._engine: BasePublishEngine | None = self._load_engine(context) if self._enabled else None
        self._capture_age = CaptureAgeWindow()

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        runtime = self._frame_runtime(context)
        frame_meta = runtime.frame_meta
        if not self._enabled:
            return self._handle_disabled(context, frame_meta)
        detections: Sequence = context.get_resource("inference_output") or []
        if runtime.is_new_frame is False:
            return self._handle_stale_frame(context, frame_meta)
        return self._process_publish(context, detections, frame_meta)

    def _handle_disabled(self, context: TaskContext, frame_meta: FrameMeta | None) -> TaskResult:
        return self._skip_result(
            context,
            frame_meta=frame_meta,
            reason="publish_disabled",
            skipped_resources={
                "publish_skipped": True,
                "publish_skip_reason": "publish_disabled",
            },
            payload={"published": 0, "status": None, "skipped": True, "reason": "publish_disabled"},
        )

    def _handle_stale_frame(self, context: TaskContext, frame_meta: FrameMeta | None) -> TaskResult:
        return self._skip_result(
            context,
            frame_meta=frame_meta,
            reason="stale_frame",
            skipped_resources={
                "publish_skipped": True,
                "publish_skip_reason": "stale_frame",
            },
            payload={"published": 0, "status": None, "skipped": True},
        )

    def _process_publish(
        self,
        context: TaskContext,
        detections: Sequence,
        frame_meta: FrameMeta | None,
    ) -> TaskResult:
        models_run = list(context.get_resource("inference_models_run") or [])
        models_reuse = list(context.get_resource("inference_models_reuse") or [])
        outcome = self._engine.publish(
            detections,
            models_run=models_run,
            models_reuse=models_reuse,
            frame_meta=frame_meta,
        )
        if outcome is None:
            raise TaskError("publish engine did not return an outcome")
        self.set_metric("published", outcome.published)
        context.set_resource("publish_skipped", False)
        context.set_resource("publish_skip_reason", None)
        result, reason, message = self._engine_result(outcome)
        if result == "success":
            self._set_capture_age_metrics(context, frame_meta)
        return self._build_task_result(
            {"published": outcome.published, "status": outcome.status},
            frame_meta,
            status=result,
            reason=reason,
            message=message,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _set_capture_age_metrics(self, context: TaskContext, frame_meta: FrameMeta | None) -> None:
        captured = frame_meta.capture_monotonic if isinstance(frame_meta, FrameMeta) else None
        if captured is None:
            return
        now = context.health.clock()
        age_ms = max(0.0, (now - captured) * 1000.0)
        age_max = self._capture_age.add(age_ms, now=now, window_s=self._health_report_interval(context))
        self.set_metric("capture_age_ms", age_ms)
        self.set_metric("capture_age_ms_max", age_max)

    def _load_engine(self, context: TaskContext | None) -> BasePublishEngine:
        engine_path = getattr(context.config, "publish_engine_class", None) if context else None
        if not engine_path:
            return MessagingPublishEngine(context=context)
        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

    def _resolve_enabled(self, context: TaskContext | None) -> bool:
        if context is None:
            return False
        publish_cfg = getattr(context.config, "publish", None)
        return bool(getattr(publish_cfg, "enabled", True))

    def _import_engine(self, path: str) -> Type[BasePublishEngine]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 Publish Engine：{path}")
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if engine_cls is None or not issubclass(engine_cls, BasePublishEngine):
            raise TaskError(f"{class_name} 必須繼承 BasePublishEngine")
        return engine_cls

    def close(self, context: TaskContext) -> list[dict]:
        _ = context
        if self._engine is None:
            return []
        close_fn = getattr(self._engine, "close", None)
        if callable(close_fn):
            result = close_fn()
            if isinstance(result, list):
                return result
            if result is not None:
                return [result]
        return []
