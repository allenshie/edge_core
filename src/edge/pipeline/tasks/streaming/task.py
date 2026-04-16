"""Streaming task delegating runtime behavior to streaming engine."""
from __future__ import annotations

import importlib
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.schema import EdgeDetection

from .engine import BaseStreamingEngine, DefaultStreamingEngine


class StreamingTask(BaseTask):
    name = "edge-streaming"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine: BaseStreamingEngine = self._load_engine(context)

    def run(self, context: TaskContext) -> TaskResult:
        frame = context.get_resource("decoded_frame")
        detections: Sequence[EdgeDetection] = context.get_resource("inference_output") or []
        phase = self._engine.resolve_phase(context) if hasattr(self._engine, "resolve_phase") else "unknown"
        status = self._engine.push(context, frame, detections, phase)
        context.set_resource("streaming_status", status.to_dict())
        context.monitor.report_event(
            "edge_streaming",
            detail=(
                f"phase={status.phase} should_stream={status.should_stream} "
                f"active={status.stream_active} q={status.queue_size} drop={status.dropped_frames}"
            ),
            component=self.name,
        )
        return TaskResult(payload={"streaming": status.to_dict()})

    def _load_engine(self, context: TaskContext | None) -> BaseStreamingEngine:
        engine_path = getattr(context.config, "streaming_engine_class", None) if context else None
        if not engine_path:
            return DefaultStreamingEngine(context=context)

        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

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

    def close(self, context: TaskContext) -> None:
        _ = context
        self._engine.close()
