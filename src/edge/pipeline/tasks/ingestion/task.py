"""Unified ingestion task with mode-based engine selection."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from .engines import BaseIngestionEngine, CameraIngestionEngine, FileIngestionEngine, RtspIngestionEngine
from edge.runtime.task_health import TaskHealthReporter
from edge.schema import FrameMeta
from edge.schema import StageStats
from .health import IngestionHealthPolicy, IngestionRecoveryPolicy

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
        self._stats = StageStats(task_name="ingest")
        self._health = TaskHealthReporter(self._stats)
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
            engine_snapshot = self._engine.health_snapshot() if self._engine is not None else {}
            self._stats.record_error(str(exc), worker_alive=self._engine.is_started())
            report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
            self._health.report_ingestion_failure(
                context,
                mode=self._mode,
                source_label=self.source_label,
                engine_snapshot=engine_snapshot,
                capture_rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
                error_message=str(exc),
                worker_alive=self._engine.is_started(),
                report_interval_seconds=report_interval,
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
        engine_snapshot = self._engine.health_snapshot() if self._engine is not None else {}
        capture_fps = self._engine.capture_rate_meter.fps() if self._engine is not None else None
        stale_threshold = float(getattr(context.config, "health_stale_threshold_seconds", 0.0) or 0.0)
        report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
        evaluation = IngestionHealthPolicy.evaluate(
            tracker_snapshot=engine_snapshot,
            mode=self._mode,
            source_label=self.source_label,
            session_id=frame_meta.session_id,
            frame_seq=frame_meta.frame_seq,
            capture_fps=capture_fps,
            capture_age_seconds=frame_meta.age_seconds(),
            stale_threshold_seconds=stale_threshold,
            worker_alive=worker_alive,
            capture_ts=frame_meta.capture_ts,
        )
        self._health.report_ingestion(
            context,
            evaluation=evaluation,
            capture_rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
            report_interval_seconds=report_interval,
        )
        recovery = IngestionRecoveryPolicy.evaluate(
            evaluation=evaluation,
            tracker_snapshot=engine_snapshot,
            recovery_cooldown_seconds=float(
                getattr(context.config, "ingestion_recovery_cooldown_seconds", 30.0) or 30.0
            ),
        )
        if recovery.action == "restart" and self._engine is not None:
            context.logger.warning(
                "%s source unhealthy, attempting restart: %s",
                self.source_label,
                recovery.reason,
            )
            try:
                self._engine.restart()
            except TaskError as exc:
                self._stats.record_error(str(exc), worker_alive=self._engine.is_started())
                LOGGER.warning("%s ingestion restart failed: %s", self.source_label, exc)
                raise
            context.monitor.report_event(
                "warning",
                detail=(
                    f"{self.source_label} recovery action=restart reason={recovery.reason} "
                    f"cooldown_remaining_s={recovery.cooldown_remaining_s}"
                ),
                component=self.name,
            )
        payload = dict(payload)
        payload.pop("frame", None)
        payload["frame_meta"] = frame_meta.to_dict()
        return TaskResult(payload=payload)

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        engine_snapshot = self._engine.health_snapshot() if self._engine is not None else {}
        stale_threshold = float(
            getattr(context.config, "health_stale_threshold_seconds", 0.0) if context is not None else 0.0
        )
        evaluation = IngestionHealthPolicy.evaluate(
            tracker_snapshot=engine_snapshot,
            mode=self._mode,
            source_label=self.source_label,
            session_id=self._stats.session_id or "",
            frame_seq=self._stats.last_frame_seq or 0,
            capture_fps=self._engine.capture_rate_meter.fps() if self._engine is not None else None,
            capture_age_seconds=self._stats.capture_age_seconds(),
            stale_threshold_seconds=stale_threshold,
            worker_alive=bool(self._engine.is_started()) if self._engine is not None else False,
        )
        return self._health.snapshot_ingestion(
            evaluation=evaluation,
            capture_rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
        )

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

    def begin_shutdown(self) -> None:
        engine = self._engine
        begin_shutdown = getattr(engine, "begin_shutdown", None) if engine is not None else None
        if callable(begin_shutdown):
            begin_shutdown()
