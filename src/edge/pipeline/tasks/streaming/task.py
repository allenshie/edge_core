"""Streaming task delegating runtime behavior to streaming engine."""
from __future__ import annotations

import importlib
import time
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.runtime.task_health import TaskHealthReporter
from edge.api.mode_server import MODE_RESOURCE
from edge.schema import EdgeDetection, FrameMeta
from edge.schema import StageStats

from .engines import BaseStreamingEngine, DefaultStreamingEngine
from .engines.policy import resolve_phase
from .types import StreamingStatus


class StreamingTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-streaming"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._enabled = self._resolve_enabled(context)
        self._engine: BaseStreamingEngine | None = self._load_engine(context) if self._enabled else None
        self._stats = StageStats(task_name="stream")
        self._last_status: StreamingStatus | None = None
        self._last_frame_meta: FrameMeta | None = None
        self._last_phase = "unknown"
        self._health = TaskHealthReporter(self._stats)
        if not self._enabled:
            self._health.report_execution(
                context,
                stage="stream",
                health_state="inactive",
                frame_meta=None,
                note="stream_disabled",
                reason="stream_disabled",
                extra_fields={
                    "phase": "disabled",
                    "should_stream": False,
                    "stream_active": False,
                    "ffmpeg_alive": False,
                    "skipped": True,
                    "reason": "stream_disabled",
                    "skip_reason": "stream_disabled",
                },
                report_interval_seconds=float(
                    getattr(context.config, "health_report_interval_seconds", 5.0) if context is not None else 5.0
                ),
                emit=False,
            )

    def run(self, context: TaskContext) -> TaskResult:
        runtime = self._frame_runtime(context)
        frame_meta = runtime.frame_meta
        phase = self._resolve_phase(context)
        if not self._enabled:
            return self._handle_disabled(context, runtime, frame_meta, phase)
        frame = context.get_resource("decoded_frame")
        detections: Sequence[EdgeDetection] = context.get_resource("inference_output") or []
        return self._process_streaming(context, runtime, frame, detections, frame_meta, phase)

    def _handle_disabled(
        self,
        context: TaskContext,
        runtime,
        frame_meta: FrameMeta | None,
        phase: str,
    ) -> TaskResult:
        summary_fields = {
            "phase": phase,
            "should_stream": False,
            "stream_active": False,
            "ffmpeg_alive": False,
            "skipped": True,
            "reason": "stream_disabled",
            "skip_reason": "stream_disabled",
        }
        self._last_status = None
        self._last_frame_meta = frame_meta
        self._last_phase = phase
        return self._report_skip(
            context,
            stage="stream",
            frame_meta=frame_meta,
            note="skipped=stream_disabled phase=%s" % phase,
            reason="stream_disabled",
            extra_fields=summary_fields,
            report_interval_seconds=runtime.report_interval_seconds,
            rate_meter=None,
            rate_prefix=None,
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
        started_at = time.perf_counter()
        if self._engine is None:
            raise TaskError("streaming engine is not initialized")
        status = self._engine.push(frame, detections, phase, frame_meta=frame_meta)
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        context.set_resource("streaming_status", status.to_dict())
        self._last_status = status
        self._last_frame_meta = frame_meta
        self._last_phase = phase
        self._record_success(
            self._stats,
            frame_meta,
            latency_ms=elapsed_ms,
            worker_alive=status.ffmpeg_alive,
            queue_size=status.queue_size,
        )
        self._health.report_streaming(
            context,
            frame_meta=frame_meta,
            phase=phase,
            status=status,
            write_rate_meter=self._engine.write_rate_meter,
            unique_write_rate_meter=self._engine.unique_write_rate_meter,
            report_interval_seconds=runtime.report_interval_seconds,
            health_threshold_seconds=runtime.stale_threshold_seconds,
        )
        return self._build_task_result({"streaming": status.to_dict()}, frame_meta)

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

    def snapshot_health(self, context: TaskContext | None = None) -> dict:
        _ = context
        if self._engine is None:
            return self._health.snapshot()
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

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        return self.snapshot_health(context)
