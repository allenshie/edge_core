"""Unified ingestion task with mode-based engine selection."""
from __future__ import annotations

import importlib
import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.schema import FrameMeta

from .engines import (
    BaseIngestionEngine,
    CameraIngestionEngine,
    FileIngestionEngine,
    RtspIngestionEngine,
)

LOGGER = logging.getLogger(__name__)

ENGINE_BY_MODE: dict[str, Type[BaseIngestionEngine]] = {
    "file": FileIngestionEngine,
    "rtsp": RtspIngestionEngine,
    "camera": CameraIngestionEngine,
}


# metric 名稱 → Engine 屬性（見 docs/HEALTH.md「Metrics」）
INGEST_COUNTERS = {"reconnects": "reconnect_count", "read_failures": "read_failure_count"}


class IngestionTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-ingestion"
    health_node = "ingest"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine: BaseIngestionEngine | None = None
        self._mode: str | None = None
        self._mode_logged = False
        self._session_id: str | None = None
        self._last_frame_meta: FrameMeta | None = None
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
        try:
            try:
                payload = self._engine.fetch()
            except TaskError as exc:
                LOGGER.warning("%s ingestion failed: %s", self.source_label, exc)
                raise
            return self._process_ingestion(context, payload)
        finally:
            # 取流失敗時也要寫入，斷線期間才看得到 read_failures 增加。
            self._set_counter_metrics(self._engine, INGEST_COUNTERS)

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
    ) -> TaskResult:
        frame_meta = self._build_frame_meta(payload)
        is_new_frame = self._is_new_frame(frame_meta)
        self._last_frame_meta = frame_meta
        self._store_pipeline_resources(context, payload, frame_meta, is_new_frame)
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
