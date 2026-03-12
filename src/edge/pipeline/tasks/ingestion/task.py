"""Unified ingestion task with mode-based engine selection."""
from __future__ import annotations

import logging
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from .engines import BaseIngestionEngine, CameraIngestionEngine, FileIngestionEngine, RtspIngestionEngine

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

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        if self._engine is None or self._mode is None:
            self._engine, self._mode = self._build_engine(context)
        if not self._mode_logged:
            context.logger.info("edge ingestion mode: %s", self._mode)
            self._mode_logged = True
        try:
            payload = self._engine.fetch(context)
        except TaskError as exc:
            LOGGER.warning("%s ingestion failed: %s", self.source_label, exc)
            raise
        return TaskResult(payload=payload)
