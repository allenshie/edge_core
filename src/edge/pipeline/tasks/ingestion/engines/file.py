from __future__ import annotations

import logging
import time

from smart_workflow import TaskContext, TaskError

from edge.config import FileSourceConfig

from ..sources import FrameSource, OpenCvFileSource
from .base import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)


class FileIngestionEngine(BaseIngestionEngine):
    source_label = "file"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._cached_config = context.config.ingestion.file if context else None

    def _validate_config(self, config: FileSourceConfig) -> None:
        if not config.path:
            raise TaskError("EDGE_FILE_PATH 尚未設定，無法啟用 file 模式")

    def _build_source(self, config: FileSourceConfig) -> FrameSource:
        LOGGER.info("Opening file ingestion source: %s", config.path)
        return OpenCvFileSource(config.path or "", loop=config.loop)

    def _get_drop_frames(self, config: FileSourceConfig) -> int:
        return max(config.drop_frames, 0)

    def _should_reconnect(self, exit_reason: str) -> bool:
        # 影片播完（未開 loop）或檔案打不開時，重開也不會有不同結果。
        return exit_reason not in {"eof", "open_failed"}

    def _get_capture_interval_seconds(self, config: FileSourceConfig, cycle_start: float) -> float:
        fps = config.fps or 0.0
        if fps <= 0:
            return 0.0
        elapsed = time.monotonic() - cycle_start
        return max(0.0, (1.0 / fps) - elapsed)
