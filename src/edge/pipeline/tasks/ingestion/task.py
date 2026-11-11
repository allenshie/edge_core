"""Unified ingestion task with mode-based engine selection."""
from __future__ import annotations

import importlib
import logging
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.runtime.task_health import TaskHealthReporter
from edge.schema import FrameMeta, StageStats

from .engines import (
    BaseIngestionEngine,
    CameraIngestionEngine,
    FileIngestionEngine,
    RtspIngestionEngine,
)
from .health import IngestionHealthEvaluation, IngestionHealthPolicy

LOGGER = logging.getLogger(__name__)

ENGINE_BY_MODE: dict[str, Type[BaseIngestionEngine]] = {
    "file": FileIngestionEngine,
    "rtsp": RtspIngestionEngine,
    "camera": CameraIngestionEngine,
}


class IngestionTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-ingestion"
    health_node = "ingest"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine: BaseIngestionEngine | None = None
        self._mode: str | None = None
        self._mode_logged = False
        self._session_id: str | None = None
        self._last_frame_meta: FrameMeta | None = None
        self._fatal_reported = False
        self._stats = StageStats(task_name="ingest")
        self._health = TaskHealthReporter(self._stats)
        if context is not None:
            self._mode = self._resolve_mode(context)
            self._engine = self._load_engine(context, self._mode)

    @property
    def engine(self) -> BaseIngestionEngine | None:
        return self._engine

    @property
    def source_label(self) -> str:
        if self._engine is None:
            return "ingestion"
        return getattr(self._engine, "source_label", "ingestion")

    def _resolve_mode(self, context: TaskContext) -> str:
        ingestion_cfg = getattr(context.config, "ingestion", None)
        mode = (ingestion_cfg.mode if ingestion_cfg else "rtsp") if hasattr(ingestion_cfg, "mode") else "rtsp"
        return (mode or "rtsp").strip().lower()

    def _load_engine(self, context: TaskContext, mode: str) -> BaseIngestionEngine:
        engine_path = getattr(context.config, "ingestion_engine_class", None)
        if engine_path:
            custom_cls = self._import_engine(engine_path)
            try:
                return custom_cls(context=context)
            except TypeError:
                return custom_cls()
        engine_cls = ENGINE_BY_MODE.get(mode)
        if engine_cls is None:
            raise TaskError(f"不支援的 ingestion mode: {mode}")
        return engine_cls(context=context)

    def _import_engine(self, path: str) -> Type[BaseIngestionEngine]:
        """INGESTION_ENGINE_CLASS：寫法和其他 *_ENGINE_CLASS 相同，類別必須繼承 BaseIngestionEngine。"""
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 Ingestion Engine：{path}")
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if not isinstance(engine_cls, type) or not issubclass(engine_cls, BaseIngestionEngine):
            raise TaskError(f"{class_name} 必須繼承 BaseIngestionEngine")
        return engine_cls

    def _ensure_session_id(self) -> str:
        if self._session_id:
            return self._session_id
        mode = self._mode or "ingest"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._session_id = f"{mode}-{timestamp}-{uuid.uuid4().hex[:8]}"
        return self._session_id

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        if self._engine is None or self._mode is None:
            raise TaskError("IngestionTask 必須在初始化時提供 TaskContext")
        if not self._mode_logged:
            context.logger.info("edge ingestion mode: %s", self._mode)
            self._mode_logged = True
        started_at = time.perf_counter()
        try:
            payload = self._engine.fetch()
        except TaskError as exc:
            self._handle_ingestion_failure(context, exc)
            LOGGER.warning("%s ingestion failed: %s", self.source_label, exc)
            raise
        return self._process_ingestion(context, payload, started_at)

    def snapshot_health(self, context: TaskContext | None = None) -> dict:
        engine_snapshot = self._engine.health_snapshot() if self._engine is not None else {}
        evaluation = self._build_ingestion_evaluation(
            context=context,
            engine_snapshot=engine_snapshot,
            frame_meta=self._last_frame_meta,
            worker_alive=bool(self._engine.is_started()) if self._engine is not None else False,
            capture_fps=self._engine.capture_rate_meter.fps() if self._engine is not None else None,
        )
        return self._health.snapshot_ingestion(
            evaluation=evaluation,
            capture_rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
        )

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        return self.snapshot_health(context)

    def _build_frame_meta(self, payload: Mapping[str, Any]) -> FrameMeta:
        capture_ts = payload.get("capture_ts")
        if not isinstance(capture_ts, datetime):
            capture_ts = datetime.now(timezone.utc)
        frame_seq = payload.get("frame_seq")
        if not isinstance(frame_seq, int) or frame_seq <= 0:
            frame_seq = 1
        capture_monotonic = payload.get("capture_monotonic")
        return FrameMeta(
            session_id=self._ensure_session_id(),
            frame_seq=frame_seq,
            capture_ts=capture_ts,
            capture_monotonic=float(capture_monotonic) if isinstance(capture_monotonic, (int, float)) else None,
        )

    def _process_ingestion(
        self,
        context: TaskContext,
        payload: Mapping[str, Any],
        started_at: float,
    ) -> TaskResult:
        frame_meta = self._build_frame_meta(payload)
        is_new_frame = self._is_new_frame(frame_meta)
        self._last_frame_meta = frame_meta
        self._store_pipeline_resources(context, payload, frame_meta, is_new_frame)
        worker_alive = bool(self._engine.health_snapshot().get("worker_alive", self._engine.is_started()))
        self._record_success(
            self._stats,
            frame_meta,
            success_ts=datetime.now(timezone.utc),
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            worker_alive=worker_alive,
        )
        engine_snapshot = self._engine.health_snapshot() if self._engine is not None else {}
        capture_fps = self._engine.capture_rate_meter.fps() if self._engine is not None else None
        evaluation = self._build_ingestion_evaluation(
            context,
            engine_snapshot=engine_snapshot,
            frame_meta=frame_meta,
            is_new_frame=is_new_frame,
            worker_alive=worker_alive,
            capture_fps=capture_fps,
        )
        self._report_ingestion(context, evaluation)
        # 只有新畫面才算成功；沒有新畫面時這一輪照常進行，健康記錄為 skipped。
        return self._build_task_result(
            self._build_ingestion_payload(payload, frame_meta, is_new_frame),
            frame_meta,
            status="success" if is_new_frame else "skipped",
            reason=None if is_new_frame else "no_new_frame",
        )

    def _is_new_frame(self, frame_meta: FrameMeta) -> bool:
        previous_frame_meta = self._last_frame_meta
        return (
            previous_frame_meta is None
            or previous_frame_meta.session_id != frame_meta.session_id
            or previous_frame_meta.frame_seq != frame_meta.frame_seq
        )

    def _store_pipeline_resources(
        self,
        context: TaskContext,
        payload: Mapping[str, Any],
        frame_meta: FrameMeta,
        is_new_frame: bool,
    ) -> None:
        frame = payload.get("frame")
        if frame is not None:
            context.set_resource("decoded_frame", frame)
        context.set_resource("decoded_frame_path", None)
        context.set_resource("frame_meta", frame_meta)
        context.set_resource("pipeline_frame_is_new", is_new_frame)

    def _build_ingestion_evaluation(
        self,
        context: TaskContext | None,
        *,
        engine_snapshot: Mapping[str, Any],
        frame_meta: FrameMeta | None,
        is_new_frame: bool | None = None,
        worker_alive: bool,
        capture_fps: float | None,
    ) -> IngestionHealthEvaluation:
        stale_threshold = self._health_stale_threshold(context) if context is not None else 0.0
        evaluation = IngestionHealthPolicy.evaluate(
            tracker_snapshot=engine_snapshot,
            mode=self._mode,
            source_label=self.source_label,
            session_id=frame_meta.session_id if frame_meta is not None else self._stats.session_id or "",
            frame_seq=frame_meta.frame_seq if frame_meta is not None else self._stats.last_frame_seq or 0,
            capture_fps=capture_fps,
            capture_age_seconds=frame_meta.age_seconds() if frame_meta is not None else self._stats.capture_age_seconds(),
            stale_threshold_seconds=stale_threshold,
            worker_alive=worker_alive,
            capture_ts=frame_meta.capture_ts if frame_meta is not None else self._stats.last_capture_ts,
        )
        if is_new_frame is not None:
            evaluation.extra_fields["is_new_frame"] = is_new_frame
            evaluation.snapshot["is_new_frame"] = is_new_frame
        return evaluation

    def _report_ingestion(self, context: TaskContext, evaluation: IngestionHealthEvaluation) -> None:
        self._health.report_ingestion(
            context,
            evaluation=evaluation,
            capture_rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
            report_interval_seconds=self._health_report_interval(context),
        )

    def _handle_ingestion_failure(self, context: TaskContext, exc: TaskError) -> None:
        engine_snapshot = self._engine.health_snapshot() if self._engine is not None else {}
        self._report_fatal(context, engine_snapshot)
        self._stats.record_error(str(exc), worker_alive=self._engine.is_started())
        self._health.report_ingestion_failure(
            context,
            mode=self._mode,
            source_label=self.source_label,
            engine_snapshot=engine_snapshot,
            capture_rate_meter=self._engine.capture_rate_meter if self._engine is not None else None,
            error_message=str(exc),
            worker_alive=self._engine.is_started(),
            report_interval_seconds=self._health_report_interval(context),
        )

    def _report_fatal(self, context: TaskContext, engine_snapshot: Mapping[str, Any]) -> None:
        """Engine 已無法自行恢復時，交給 liveness 讓 k8s 重啟 pod；只回報一次。"""
        if self._fatal_reported or not engine_snapshot.get("fatal"):
            return
        self._fatal_reported = True
        reason = f"{self.source_label} ingestion fatal: {engine_snapshot.get('fatal_reason')}"
        LOGGER.critical(reason)
        context.monitor.report_event("error", detail=reason, component=self.name)
        mark_fatal = getattr(context.get_resource("health_state"), "mark_fatal", None)
        if callable(mark_fatal):
            mark_fatal(reason)

    def _build_ingestion_payload(
        self,
        payload: Mapping[str, Any],
        frame_meta: FrameMeta,
        is_new_frame: bool,
    ) -> dict[str, Any]:
        result = dict(payload)
        result.pop("frame", None)
        result.pop("capture_monotonic", None)
        result["frame_meta"] = frame_meta.to_dict()
        result["is_new_frame"] = is_new_frame
        return result

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
