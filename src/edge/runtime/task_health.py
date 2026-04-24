"""Reusable task health presentation helper."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from edge.runtime.rate_meter import RateMeter
from edge.runtime.stage_logging import emit_task_health
from edge.schema import FrameMeta
from edge.schema import StageStats


class TaskHealthReporter:
    """Format, emit, and cache task health snapshots."""

    def __init__(self, stats: StageStats) -> None:
        self._stats = stats
        self._last_snapshot: dict[str, Any] | None = None

    def build_snapshot(
        self,
        *,
        stage: str,
        state: str,
        session_id: str | None,
        frame_seq: int | None,
        capture_fps: float | None,
        infer_fps: float | None,
        stream_output_fps: float | None,
        stream_unique_fps: float | None,
        age_s: float | None,
        alive: bool,
        note: str,
        extra_fields: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "stage": stage,
            "state": state,
            "session_id": session_id,
            "frame_seq": frame_seq,
            "capture_fps": capture_fps,
            "infer_fps": infer_fps,
            "stream_output_fps": stream_output_fps,
            "stream_unique_fps": stream_unique_fps,
            "age_s": age_s,
            "alive": alive,
            "note": note,
        }
        if extra_fields:
            snapshot.update(dict(extra_fields))
        return snapshot

    def emit(
        self,
        context: Any,
        *,
        health_state: str,
        reason: str | None = None,
        worker_alive: bool | None = None,
        queue_size: int | None = None,
        extra_fields: Mapping[str, Any] | None = None,
        event_type: str | None = None,
        report_interval_seconds: float | None = None,
        level: int | None = None,
        force: bool = False,
        rate_meter: RateMeter | None = None,
        rate_prefix: str | None = None,
        snapshot: Mapping[str, Any] | None = None,
    ) -> str | None:
        line = emit_task_health(
            context,
            self._stats,
            health_state=health_state,
            reason=reason,
            worker_alive=worker_alive,
            queue_size=queue_size,
            extra_fields=extra_fields,
            event_type=event_type,
            report_interval_seconds=report_interval_seconds,
            level=level,
            force=force,
            rate_meter=rate_meter,
            rate_prefix=rate_prefix,
        )
        if line is not None and snapshot is not None:
            self._last_snapshot = dict(snapshot)
        return line

    def snapshot(self, fallback_snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self._last_snapshot is not None:
            return dict(self._last_snapshot)
        if fallback_snapshot is not None:
            return dict(fallback_snapshot)
        return self._stats.snapshot()

    def report_inference(
        self,
        context: Any,
        *,
        frame_meta: FrameMeta | None,
        detections_count: int,
        model_config: Any | None,
        infer_rate_meter: RateMeter,
        report_interval_seconds: float,
        stale_threshold_seconds: float,
    ) -> str | None:
        model_name = getattr(model_config, "name", None)
        model_device = getattr(model_config, "device", None)
        capture_age_s = frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else None
        is_stale = bool(
            stale_threshold_seconds > 0
            and capture_age_s is not None
            and capture_age_s >= stale_threshold_seconds
        )
        health_state = "degraded" if is_stale else "ok"
        summary_fields = {
            "model": model_name,
            "device": model_device,
            "detections": detections_count,
        }
        snapshot = self.build_snapshot(
            stage="infer",
            state=health_state,
            session_id=self._stats.session_id,
            frame_seq=self._stats.last_frame_seq,
            capture_fps=None,
            infer_fps=infer_rate_meter.fps(),
            stream_output_fps=None,
            stream_unique_fps=None,
            age_s=capture_age_s,
            alive=bool(self._stats.worker_alive),
            note=f"model={model_name} device={model_device} detections={detections_count}",
            extra_fields=summary_fields,
        )
        return self.emit(
            context,
            health_state=health_state,
            reason="stale_frame" if is_stale else None,
            worker_alive=True,
            extra_fields=summary_fields,
            report_interval_seconds=report_interval_seconds,
            force=False,
            rate_meter=infer_rate_meter,
            rate_prefix="infer",
            snapshot=snapshot,
        )

    def snapshot_inference(
        self,
        *,
        frame_meta: FrameMeta | None,
        detections_count: int,
        model_config: Any | None,
        infer_rate_meter: RateMeter,
    ) -> dict[str, Any]:
        model_name = getattr(model_config, "name", None)
        model_device = getattr(model_config, "device", None)
        fallback = self.build_snapshot(
            stage="infer",
            state=self._stats.health_state,
            session_id=self._stats.session_id,
            frame_seq=self._stats.last_frame_seq,
            capture_fps=None,
            infer_fps=infer_rate_meter.fps(),
            stream_output_fps=None,
            stream_unique_fps=None,
            age_s=frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else self._stats.capture_age_seconds(),
            alive=bool(self._stats.worker_alive),
            note=f"model={model_name} device={model_device} detections={detections_count}",
        )
        return self.snapshot(fallback_snapshot=fallback)

    def report_publish(
        self,
        context: Any,
        *,
        frame_meta: FrameMeta | None,
        outcome: Any,
        stale_threshold_seconds: float,
        report_interval_seconds: float,
    ) -> str | None:
        published = getattr(outcome, "published", 0)
        status = getattr(outcome, "status", None)
        capture_age_s = frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else None
        status_ok = status is not None
        is_stale = bool(
            stale_threshold_seconds > 0
            and capture_age_s is not None
            and capture_age_s >= stale_threshold_seconds
        )
        health_state = "ok" if status_ok and not is_stale else "degraded" if status_ok else "error"
        summary_fields = {
            "published": published,
            "status": status,
        }
        snapshot = self.build_snapshot(
            stage="publish",
            state=health_state,
            session_id=self._stats.session_id,
            frame_seq=self._stats.last_frame_seq,
            capture_fps=None,
            infer_fps=None,
            stream_output_fps=None,
            stream_unique_fps=None,
            age_s=capture_age_s,
            alive=bool(self._stats.worker_alive),
            note=f"published={published} status={status}",
            extra_fields=summary_fields,
        )
        summary_line = self.emit(
            context,
            health_state=health_state,
            reason="integration_api_unreachable" if not status_ok else "stale_frame" if is_stale else None,
            worker_alive=True,
            extra_fields=summary_fields,
            report_interval_seconds=report_interval_seconds,
            event_type="edge_publish",
            level=logging.WARNING if not status_ok or is_stale else logging.INFO,
            force=False,
            snapshot=snapshot,
        )
        if summary_line is not None and not status_ok:
            monitor = getattr(context, "monitor", None)
            report_event = getattr(monitor, "report_event", None)
            if callable(report_event):
                report_event("warning", detail=summary_line, component="edge-publish")
        return summary_line

    def snapshot_publish(
        self,
        *,
        frame_meta: FrameMeta | None,
        outcome: Any,
    ) -> dict[str, Any]:
        published = getattr(outcome, "published", 0)
        status = getattr(outcome, "status", None)
        fallback = self.build_snapshot(
            stage="publish",
            state=self._stats.health_state,
            session_id=self._stats.session_id,
            frame_seq=self._stats.last_frame_seq,
            capture_fps=None,
            infer_fps=None,
            stream_output_fps=None,
            stream_unique_fps=None,
            age_s=frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else self._stats.capture_age_seconds(),
            alive=bool(self._stats.worker_alive),
            note=f"published={published} status={status}",
        )
        return self.snapshot(fallback_snapshot=fallback)

    def report_streaming(
        self,
        context: Any,
        *,
        frame_meta: FrameMeta | None,
        phase: str,
        status: Any,
        write_rate_meter: RateMeter,
        unique_write_rate_meter: RateMeter,
        report_interval_seconds: float,
        health_threshold_seconds: float,
    ) -> str | None:
        should_stream = bool(getattr(status, "should_stream", False))
        stream_active = bool(getattr(status, "stream_active", False))
        ffmpeg_alive = bool(getattr(status, "ffmpeg_alive", False))
        dropped_frames = int(getattr(status, "dropped_frames", 0) or 0)
        processed_frames = int(getattr(status, "processed_frames", 0) or 0)
        no_frame_seconds = float(getattr(status, "no_frame_seconds", 0.0) or 0.0)
        since_last_write_seconds = float(getattr(status, "since_last_write_seconds", 0.0) or 0.0)
        queue_size = int(getattr(status, "queue_size", 0) or 0)
        last_error = getattr(status, "last_error", None)
        capture_age_s = frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else None
        is_stale = bool(
            health_threshold_seconds > 0
            and (
                no_frame_seconds >= health_threshold_seconds
                or since_last_write_seconds >= health_threshold_seconds
                or (capture_age_s is not None and capture_age_s >= health_threshold_seconds)
            )
        )
        if not should_stream:
            health_state = "disabled"
            reason = "phase_disabled"
        elif last_error or is_stale:
            health_state = "degraded"
            reason = last_error or "stale_frame"
        elif not stream_active:
            health_state = "stalled"
            reason = "stream_inactive"
        else:
            health_state = "ok"
            reason = None
        stream_output_fps = write_rate_meter.fps()
        stream_unique_fps = unique_write_rate_meter.fps()
        summary_fields = {
            "phase": phase,
            "should_stream": should_stream,
            "stream_active": stream_active,
            "ffmpeg_alive": ffmpeg_alive,
            "dropped_frames": dropped_frames,
            "processed_frames": processed_frames,
            "no_frame_s": no_frame_seconds,
            "no_write_s": since_last_write_seconds,
            "threshold_s": health_threshold_seconds,
            "stream_output_fps": stream_output_fps,
            "stream_unique_fps": stream_unique_fps,
        }
        if reason is not None:
            summary_fields["reason"] = reason
        snapshot = self.build_snapshot(
            stage="stream",
            state=health_state,
            session_id=self._stats.session_id,
            frame_seq=self._stats.last_frame_seq,
            capture_fps=None,
            infer_fps=None,
            stream_output_fps=stream_output_fps,
            stream_unique_fps=stream_unique_fps,
            age_s=capture_age_s,
            alive=ffmpeg_alive,
            note=f"phase={phase} should_stream={should_stream} ffmpeg={ffmpeg_alive}",
            extra_fields=summary_fields,
        )
        summary_line = self.emit(
            context,
            health_state=health_state,
            reason=reason,
            worker_alive=ffmpeg_alive,
            queue_size=queue_size,
            extra_fields=summary_fields,
            report_interval_seconds=report_interval_seconds,
            event_type="warning" if health_state in {"degraded", "stalled"} else "edge_streaming",
            level=logging.WARNING if health_state in {"degraded", "stalled"} else logging.INFO,
            snapshot=snapshot,
        )
        if summary_line is not None:
            write_rate_meter.mark_reported()
            unique_write_rate_meter.mark_reported()
        return summary_line

    def snapshot_streaming(
        self,
        *,
        frame_meta: FrameMeta | None,
        phase: str,
        status: Any,
        write_rate_meter: RateMeter,
        unique_write_rate_meter: RateMeter,
        health_threshold_seconds: float,
    ) -> dict[str, Any]:
        should_stream = bool(getattr(status, "should_stream", False))
        stream_active = bool(getattr(status, "stream_active", False))
        ffmpeg_alive = bool(getattr(status, "ffmpeg_alive", False))
        last_error = getattr(status, "last_error", None)
        capture_age_s = frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else self._stats.last_success_age_seconds()
        stream_output_fps = write_rate_meter.fps()
        stream_unique_fps = unique_write_rate_meter.fps()
        if not should_stream:
            health_state = "disabled"
        elif last_error or (
            health_threshold_seconds > 0
            and (
                self._stats.last_success_age_seconds() >= health_threshold_seconds
                or (capture_age_s is not None and capture_age_s >= health_threshold_seconds)
            )
        ):
            health_state = "degraded"
        elif not stream_active:
            health_state = "stalled"
        else:
            health_state = self._stats.health_state
        fallback = self.build_snapshot(
            stage="stream",
            state=health_state,
            session_id=self._stats.session_id,
            frame_seq=self._stats.last_frame_seq,
            capture_fps=None,
            infer_fps=None,
            stream_output_fps=stream_output_fps,
            stream_unique_fps=stream_unique_fps,
            age_s=capture_age_s,
            alive=ffmpeg_alive,
            note=f"phase={phase} should_stream={should_stream} ffmpeg={ffmpeg_alive}",
        )
        return self.snapshot(fallback_snapshot=fallback)

    def report_ingestion(
        self,
        context: Any,
        *,
        evaluation: Any,
        capture_rate_meter: RateMeter | None,
        report_interval_seconds: float,
    ) -> str | None:
        summary_line = self.emit(
            context,
            health_state=evaluation.health_state,
            reason=evaluation.reason,
            worker_alive=bool(getattr(evaluation, "worker_alive", True)),
            extra_fields=evaluation.extra_fields,
            report_interval_seconds=report_interval_seconds,
            force=False,
            rate_meter=capture_rate_meter,
            rate_prefix="capture",
            snapshot=evaluation.snapshot,
        )
        return summary_line

    def report_ingestion_failure(
        self,
        context: Any,
        *,
        mode: str | None,
        source_label: str,
        engine_snapshot: Mapping[str, Any],
        capture_rate_meter: RateMeter | None,
        error_message: str,
        worker_alive: bool,
        report_interval_seconds: float,
    ) -> str | None:
        summary_fields = {
            "mode": mode,
            "source": source_label,
            "source_health": engine_snapshot.get("source_health_state"),
            "read_failures": engine_snapshot.get("read_failure_count"),
            "consecutive_failures": engine_snapshot.get("consecutive_read_failures"),
            "reconnect_count": engine_snapshot.get("reconnect_count"),
            "last_source_issue": engine_snapshot.get("last_read_failure_reason"),
            "last_reconnect_ts": engine_snapshot.get("last_reconnect_ts"),
        }
        snapshot = self.build_snapshot(
            stage="ingest",
            state="error",
            session_id=self._stats.session_id,
            frame_seq=self._stats.last_frame_seq,
            capture_fps=capture_rate_meter.fps() if capture_rate_meter is not None else None,
            infer_fps=None,
            stream_output_fps=None,
            stream_unique_fps=None,
            age_s=self._stats.capture_age_seconds(),
            alive=worker_alive,
            note=(
                f"mode={mode or 'rtsp'} source={source_label} "
                f"source_health={engine_snapshot.get('source_health_state')} "
                f"read_failures={engine_snapshot.get('read_failure_count')}"
            ),
            extra_fields=summary_fields,
        )
        return self.emit(
            context,
            health_state="error",
            reason=error_message,
            worker_alive=worker_alive,
            extra_fields=summary_fields,
            force=True,
            level=logging.WARNING,
            report_interval_seconds=report_interval_seconds,
            rate_meter=capture_rate_meter,
            rate_prefix="capture",
            snapshot=snapshot,
        )

    def snapshot_ingestion(
        self,
        *,
        evaluation: Any,
        capture_rate_meter: RateMeter | None,
    ) -> dict[str, Any]:
        return self.snapshot(fallback_snapshot=evaluation.snapshot)
