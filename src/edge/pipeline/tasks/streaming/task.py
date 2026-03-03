"""Streaming task delegating runtime behavior to streaming engine."""
from __future__ import annotations

from typing import Sequence

from smart_workflow import BaseTask, TaskContext, TaskResult

from edge.schema import EdgeDetection

from .engine import BaseStreamingEngine, DefaultStreamingEngine


class StreamingTask(BaseTask):
    name = "edge-streaming"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine: BaseStreamingEngine = DefaultStreamingEngine(context=context)

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

    def close(self, context: TaskContext) -> None:
        _ = context
        self._engine.close()

