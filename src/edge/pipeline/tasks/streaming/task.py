"""Streaming task delegating runtime behavior to streaming engine."""
from __future__ import annotations

import importlib
import logging
import time
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.runtime.stage_logging import emit_task_health
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
        self._last_health_snapshot: dict | None = None

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
        self._report_health(context, frame_meta, status, phase)
        frame_meta_payload = frame_meta.to_dict() if isinstance(frame_meta, FrameMeta) else None
        payload = {"streaming": status.to_dict()}
        if frame_meta_payload is not None:
            payload["frame_meta"] = frame_meta_payload
        return TaskResult(payload=payload)

    def _report_health(
        self,
        context: TaskContext,
        frame_meta: FrameMeta | object,
        status,
        phase: str,
    ) -> None:
        health_threshold = float(getattr(context.config, "health_stale_threshold_seconds", 0.0) or 0.0)
        report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
        capture_age_s = frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else None
        is_stale = bool(
            health_threshold > 0
            and (
                status.no_frame_seconds >= health_threshold
                or status.since_last_write_seconds >= health_threshold
                or (capture_age_s is not None and capture_age_s >= health_threshold)
            )
        )
        if not status.should_stream:
            health_state = "disabled"
            reason = "phase_disabled"
        elif status.last_error or is_stale:
            health_state = "degraded"
            reason = status.last_error or "stale_frame"
        elif not status.stream_active:
            health_state = "stalled"
            reason = "stream_inactive"
        else:
            health_state = "ok"
            reason = None

        summary_fields = {
            "phase": phase,
            "should_stream": status.should_stream,
            "stream_active": status.stream_active,
            "ffmpeg_alive": status.ffmpeg_alive,
            "dropped_frames": status.dropped_frames,
            "processed_frames": status.processed_frames,
            "no_frame_s": status.no_frame_seconds,
            "no_write_s": status.since_last_write_seconds,
            "threshold_s": health_threshold,
        }
        stream_output_fps = self._engine.write_rate_meter.fps()
        stream_unique_fps = self._engine.unique_write_rate_meter.fps()
        summary_fields["stream_output_fps"] = stream_output_fps
        summary_fields["stream_unique_fps"] = stream_unique_fps
        if reason is not None:
            summary_fields["reason"] = reason

        snapshot = {
            "stage": "stream",
            "state": health_state,
            "session_id": self._stats.session_id,
            "frame_seq": self._stats.last_frame_seq,
            "capture_fps": None,
            "infer_fps": None,
            "stream_output_fps": stream_output_fps,
            "stream_unique_fps": stream_unique_fps,
            "age_s": capture_age_s,
            "alive": status.ffmpeg_alive,
            "note": f"phase={phase} should_stream={status.should_stream} ffmpeg={status.ffmpeg_alive}",
        }

        if health_state in {"degraded", "stalled"}:
            summary_line = emit_task_health(
                context,
                self._stats,
                health_state=health_state,
                reason=reason,
                worker_alive=status.ffmpeg_alive,
                queue_size=status.queue_size,
                extra_fields=summary_fields,
                report_interval_seconds=report_interval,
                event_type="warning",
                level=logging.WARNING,
            )
            if summary_line is not None:
                self._engine.write_rate_meter.mark_reported()
                self._engine.unique_write_rate_meter.mark_reported()
                self._last_health_snapshot = snapshot
            return

        summary_line = emit_task_health(
            context,
            self._stats,
            health_state=health_state,
            reason=reason,
            worker_alive=status.ffmpeg_alive,
            queue_size=status.queue_size,
            extra_fields=summary_fields,
            report_interval_seconds=report_interval,
            event_type="edge_streaming",
            level=logging.INFO,
        )
        if summary_line is not None:
            self._engine.write_rate_meter.mark_reported()
            self._engine.unique_write_rate_meter.mark_reported()
            self._last_health_snapshot = snapshot

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

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        _ = context
        if self._last_health_snapshot is not None:
            return dict(self._last_health_snapshot)
        status = self._last_status
        frame_meta = self._last_frame_meta
        stream_output_fps = self._engine.write_rate_meter.fps()
        stream_unique_fps = self._engine.unique_write_rate_meter.fps()
        return {
            "stage": "stream",
            "state": self._stats.health_state,
            "session_id": self._stats.session_id,
            "frame_seq": self._stats.last_frame_seq,
            "capture_fps": None,
            "infer_fps": None,
            "stream_output_fps": stream_output_fps,
            "stream_unique_fps": stream_unique_fps,
            "age_s": frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else self._stats.last_success_age_seconds(),
            "alive": bool(status.ffmpeg_alive) if status is not None else self._stats.worker_alive,
            "note": (
                f"phase={self._last_phase} should_stream={status.should_stream} "
                f"ffmpeg={status.ffmpeg_alive}"
            )
            if status is not None
            else "ffmpeg=-",
        }
