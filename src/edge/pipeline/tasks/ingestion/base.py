"""Shared engine-backed ingestion task helpers."""
from __future__ import annotations

import logging
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from .engine import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)


class BaseIngestionTask(BaseTask):
    """Edge ingestion 任務統一委派到 BaseIngestionEngine。"""

    engine_cls: Type[BaseIngestionEngine] | None = None

    def __init__(self, context: TaskContext | None = None) -> None:
        if self.engine_cls is None:
            raise TaskError("未指定 ingestion engine")
        self._engine: BaseIngestionEngine = self.engine_cls(context=context)

    @property
    def source_label(self) -> str:
        return getattr(self._engine, "source_label", "ingestion")

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        try:
            payload = self._engine.fetch(context)
        except TaskError as exc:
            LOGGER.warning("%s ingestion failed: %s", self.source_label, exc)
            raise
        return TaskResult(payload=payload)
