"""Unified ingestion task with mode-based engine selection."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from .engines import BaseIngestionEngine, CameraIngestionEngine, FileIngestionEngine, RtspIngestionEngine
from edge.schema import FrameMeta
from edge.runtime.stage_logging import emit_task_health
from edge.schema import StageStats

LOGGER = logging.getLogger(__name__)

ENGINE_BY_MODE: dict[str, Type[BaseIngestionEngine]] = {
    "file": FileIngestionEngine,
    "rtsp": RtspIngestionEngine,
    "camera": CameraIngestionEngine,
}


class IngestionTask(BaseTask):
    name = "edge-ingestion"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine: BaseIngestionEngine | None = None
        self._mode: str | None = None
        self._mode_logged = False
        self._session_id: str | None = None
        self._last_frame_meta: FrameMeta | None = None
        self._last_health_snapshot: dict | None = None
        self._stats = StageStats(task_name="ingest")
        if context is not None:
            self._engine, self._mode = self._build_engine(context)

    @property
    def source_label(self) -> str:
        if self._engine is None:
            return "ingestion"
        return getattr(self._engine, "source_label", "ingestion")

    def _resolve_mode(self, context: TaskContext) -> str:
        ingestion_cfg = getattr(context.config, "ingestion", None)
        mode = (ingestion_cfg.mode if ingestion_cfg else "rtsp") if hasattr(ingestion_cfg, "mode") else "rtsp"
        return (mode or "rtsp").strip().lower()

    def _build_engine(self, context: TaskContext) -> tuple[BaseIngestionEngine, str]:
        mode = self._resolve_mode(context)
        engine_cls = ENGINE_BY_MODE.get(mode)
        if engine_cls is None:
            raise TaskError(f"不支援的 ingestion mode: {mode}")
        return engine_cls(context=context), mode

    def _ensure_session_id(self) -> str:
        if self._session_id:
            return self._session_id
        mode = self._mode or "ingest"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._session_id = f"{mode}-{timestamp}-{uuid.uuid4().hex[:8]}"
        return self._session_id

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        if self._engine is None or self._mode is None:
            self._engine, self._mode = self._build_engine(context)
        if not self._mode_logged:
            context.logger.info("edge ingestion mode: %s", self._mode)
            self._mode_logged = True
        started_at = time.perf_counter()
        try:
            payload = self._engine.fetch()
        except TaskError as exc:
            self._stats.record_error(str(exc), worker_alive=self._engine.is_started())
            emit_task_health(
                context,
                self._stats,
                health_state="error",
                reason=str(exc),
                worker_alive=self._engine.is_started(),
                extra_fields={"mode": self._mode, "source": self.source_label},
                force=True,
                level=logging.WARNING,
                rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
                rate_prefix="capture",
            )
            LOGGER.warning("%s ingestion failed: %s", self.source_label, exc)
            raise
        capture_ts = payload.get("capture_ts")
        frame = payload.get("frame")
        if not isinstance(capture_ts, datetime):
            capture_ts = datetime.now(timezone.utc)
        frame_seq = payload.get("frame_seq")
        if not isinstance(frame_seq, int) or frame_seq <= 0:
            frame_seq = 1
        frame_meta = FrameMeta(
            session_id=self._ensure_session_id(),
            frame_seq=frame_seq,
            capture_ts=capture_ts,
        )
        self._last_frame_meta = frame_meta
        if frame is not None:
            context.set_resource("decoded_frame", frame)
        context.set_resource("decoded_frame_path", None)
        context.set_resource("frame_meta", frame_meta)
        worker_alive = bool(self._engine.health_snapshot().get("worker_alive", self._engine.is_started()))
        self._stats.record_success(
            session_id=frame_meta.session_id,
            frame_seq=frame_meta.frame_seq,
            capture_ts=frame_meta.capture_ts,
            success_ts=datetime.now(timezone.utc),
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            worker_alive=worker_alive,
        )
        self._report_health(context, frame_meta, worker_alive=worker_alive)
        payload = dict(payload)
        payload.pop("frame", None)
        payload["frame_meta"] = frame_meta.to_dict()
        return TaskResult(payload=payload)

    def _report_health(self, context: TaskContext, frame_meta: FrameMeta, *, worker_alive: bool) -> None:
        report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
        stale_threshold = float(getattr(context.config, "health_stale_threshold_seconds", 0.0) or 0.0)
        capture_age_s = frame_meta.age_seconds()
        health_state = "degraded" if stale_threshold > 0 and capture_age_s >= stale_threshold else "ok"
        capture_fps = self._engine.capture_rate_meter.fps() if self._engine is not None else None
        snapshot = {
            "stage": "ingest",
            "state": health_state,
            "session_id": frame_meta.session_id,
            "frame_seq": frame_meta.frame_seq,
            "capture_fps": capture_fps,
            "infer_fps": None,
            "stream_output_fps": None,
            "stream_unique_fps": None,
            "age_s": capture_age_s,
            "alive": worker_alive,
            "note": f"mode={self._mode or 'rtsp'} source={self.source_label}",
        }
        line = emit_task_health(
            context,
            self._stats,
            health_state=health_state,
            reason="stale_capture" if health_state == "degraded" else None,
            worker_alive=worker_alive,
            extra_fields={
                "mode": self._mode,
                "source": self.source_label,
                "capture_ts": frame_meta.capture_ts,
            },
            report_interval_seconds=report_interval,
            force=False,
            rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
            rate_prefix="capture",
        )
        if line is not None:
            self._last_health_snapshot = snapshot

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        _ = context
        if self._last_health_snapshot is not None:
            return dict(self._last_health_snapshot)
        capture_fps = self._engine.capture_rate_meter.fps() if self._engine is not None else None
        return {
            "stage": "ingest",
            "state": self._stats.health_state,
            "session_id": self._stats.session_id,
            "frame_seq": self._stats.last_frame_seq,
            "capture_fps": capture_fps,
            "infer_fps": None,
            "stream_output_fps": None,
            "stream_unique_fps": None,
            "age_s": self._stats.capture_age_seconds(),
            "alive": bool(self._engine.is_started()) if self._engine is not None else False,
            "note": f"mode={self._mode or 'rtsp'} source={self.source_label}",
        }

    def close(self, context: TaskContext) -> list[dict]:
        _ = context
        engine = self._engine
        if engine is None:
            return []
        close_fn = getattr(engine, "close", None)
        if callable(close_fn):
            result = close_fn()
            if isinstance(result, list):
                return result
            if result is not None:
                return [result]
        return []
