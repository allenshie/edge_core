"""Streaming task delegating runtime behavior to streaming engine."""
from __future__ import annotations

import importlib
import time
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.runtime.task_health import TaskHealthReporter
from edge.api.mode_server import MODE_RESOURCE
from edge.schema import EdgeDetection, FrameMeta
from edge.schema import StageStats

from .engines import BaseStreamingEngine, DefaultStreamingEngine
from .engines.policy import resolve_phase
from .types import StreamingStatus


class StreamingTask(BaseTask):
    name = "edge-streaming"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine: BaseStreamingEngine = self._load_engine(context)
        self._stats = StageStats(task_name="stream")
        self._last_status: StreamingStatus | None = None
        self._last_frame_meta: FrameMeta | None = None
        self._last_phase = "unknown"
        self._health = TaskHealthReporter(self._stats)

    def run(self, context: TaskContext) -> TaskResult:
        started_at = time.perf_counter()
        frame = context.get_resource("decoded_frame")
        detections: Sequence[EdgeDetection] = context.get_resource("inference_output") or []
        frame_meta = context.get_resource("frame_meta")
        phase = self._resolve_phase(context)
        status = self._engine.push(frame, detections, phase, frame_meta=frame_meta)
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        context.set_resource("streaming_status", status.to_dict())
        self._last_status = status
        self._last_frame_meta = frame_meta if isinstance(frame_meta, FrameMeta) else None
        self._last_phase = phase
        self._stats.record_success(
            session_id=frame_meta.session_id if isinstance(frame_meta, FrameMeta) else None,
            frame_seq=frame_meta.frame_seq if isinstance(frame_meta, FrameMeta) else None,
            capture_ts=frame_meta.capture_ts if isinstance(frame_meta, FrameMeta) else None,
            success_ts=None,
            latency_ms=elapsed_ms,
            worker_alive=status.ffmpeg_alive,
            queue_size=status.queue_size,
        )
        report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
        health_threshold = float(getattr(context.config, "health_stale_threshold_seconds", 0.0) or 0.0)
        self._health.report_streaming(
            context,
            frame_meta=frame_meta if isinstance(frame_meta, FrameMeta) else None,
            phase=phase,
            status=status,
            write_rate_meter=self._engine.write_rate_meter,
            unique_write_rate_meter=self._engine.unique_write_rate_meter,
            report_interval_seconds=report_interval,
            health_threshold_seconds=health_threshold,
        )
        frame_meta_payload = frame_meta.to_dict() if isinstance(frame_meta, FrameMeta) else None
        payload = {"streaming": status.to_dict()}
        if frame_meta_payload is not None:
            payload["frame_meta"] = frame_meta_payload
        return TaskResult(payload=payload)

    def _resolve_phase(self, context: TaskContext) -> str:
        phase = context.get_resource(MODE_RESOURCE)
        return resolve_phase(phase)

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

    def begin_shutdown(self) -> None:
        begin_shutdown = getattr(self._engine, "begin_shutdown", None)
        if callable(begin_shutdown):
            begin_shutdown()

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        _ = context
        return self._health.snapshot_streaming(
            frame_meta=self._last_frame_meta,
            phase=self._last_phase,
            status=self._last_status,
            write_rate_meter=self._engine.write_rate_meter,
            unique_write_rate_meter=self._engine.unique_write_rate_meter,
            health_threshold_seconds=float(
                getattr(context.config, "health_stale_threshold_seconds", 0.0) if context is not None else 0.0
            ),
        )
