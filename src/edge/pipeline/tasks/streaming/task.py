"""Streaming task delegating runtime behavior to streaming engine."""
from __future__ import annotations

import importlib
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.schema import EdgeDetection, FrameMeta

from .engines import BaseStreamingEngine, DefaultStreamingEngine
from .engines.policy import resolve_phase


# metric 名稱 → Engine 屬性（spec-edge.md 9.2）
STREAM_COUNTERS = {"ffmpeg_restarts": "ffmpeg_restarts", "write_failures": "write_failures"}


class StreamingTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-streaming"
    health_node = "stream"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._enabled = self._resolve_enabled(context)
        self._engine: BaseStreamingEngine | None = self._load_engine(context) if self._enabled else None

    def run(self, context: TaskContext) -> TaskResult:
        runtime = self._frame_runtime(context)
        frame_meta = runtime.frame_meta
        phase = self._resolve_phase(context)
        if not self._enabled:
            return self._handle_disabled(context, runtime, frame_meta, phase)
        frame = context.get_resource("decoded_frame")
        detections: Sequence[EdgeDetection] = context.get_resource("inference_output") or []
        return self._process_streaming(context, runtime, frame, detections, frame_meta, phase)

    def _handle_disabled(self, context: TaskContext, runtime, frame_meta: FrameMeta | None, phase: str) -> TaskResult:
        return self._skip_result(
            context,
            frame_meta=frame_meta,
            reason="stream_disabled",
            skipped_resources={
                "streaming_skipped": True,
                "streaming_skip_reason": "stream_disabled",
            },
            payload={"streaming": None, "skipped": True, "reason": "stream_disabled"},
        )

    def _process_streaming(
        self,
        context: TaskContext,
        runtime,
        frame,
        detections: Sequence[EdgeDetection],
        frame_meta: FrameMeta | None,
        phase: str,
    ) -> TaskResult:
        if self._engine is None:
            raise TaskError("streaming engine is not initialized")
        try:
            status = self._engine.push(frame, detections, phase, frame_meta=frame_meta)
        finally:
            self._set_counter_metrics(self._engine, STREAM_COUNTERS)
        context.set_resource("streaming_status", status.to_dict())
        if runtime.is_new_frame is False:
            # 沒有新畫面時照舊呼叫 push()（業務行為不變），健康記錄為 skipped。
            result, reason, message = "skipped", "stale_frame", None
        else:
            result, reason, message = self._engine_result(status)
        return self._build_task_result(
            {"streaming": status.to_dict()}, frame_meta, status=result, reason=reason, message=message
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def engine(self) -> BaseStreamingEngine | None:
        return self._engine

    def current_phase(self, context: TaskContext) -> str:
        """目前的時段，解析方式和推流時相同；供健康架構的 enabled 判斷使用。"""
        return self._resolve_phase(context)

    def _resolve_phase(self, context: TaskContext) -> str:
        phase_resource_name = self._resolve_phase_resource_name(context)
        phase = context.get_resource(phase_resource_name)
        return resolve_phase(phase)

    def _resolve_phase_resource_name(self, context: TaskContext) -> str:
        phase_cfg = getattr(context.config, "phase_messaging", None)
        resource_name = getattr(phase_cfg, "resource_name", None)
        return str(resource_name or "edge_mode")

    def _load_engine(self, context: TaskContext | None) -> BaseStreamingEngine:
        engine_path = getattr(context.config, "streaming_engine_class", None) if context else None
        if not engine_path:
            return DefaultStreamingEngine(context=context)

        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

    def _resolve_enabled(self, context: TaskContext | None) -> bool:
        if context is None:
            return False
        streaming_cfg = getattr(context.config, "streaming", None)
        return bool(getattr(streaming_cfg, "enabled", False))

    def _import_engine(self, path: str) -> Type[BaseStreamingEngine]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 Streaming Engine：{path}")

        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if engine_cls is None or not issubclass(engine_cls, BaseStreamingEngine):
            raise TaskError(f"{class_name} 必須繼承 BaseStreamingEngine")
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

    def begin_shutdown(self) -> None:
        if self._engine is None:
            return
        begin_shutdown = getattr(self._engine, "begin_shutdown", None)
        if callable(begin_shutdown):
            begin_shutdown()
