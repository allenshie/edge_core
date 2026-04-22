from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2  # type: ignore[import]

from smart_workflow import TaskContext, TaskError

from edge.config import FileSourceConfig

from .base import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)


class FileIngestionEngine(BaseIngestionEngine):
    source_label = "file"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._file_config = context.config.ingestion.file if context else None
        self._cached_config = self._file_config

    def _get_config(self, context: TaskContext) -> FileSourceConfig:
        cfg = self._file_config
        if cfg is None:
            ingestion = getattr(context.config, "ingestion", None)
            cfg = ingestion.file if ingestion else None
        if cfg is None:
            raise TaskError("找不到 file ingestion 設定")
        if not cfg.path:
            raise TaskError("EDGE_FILE_PATH 尚未設定，無法啟用 file 模式")
        return cfg

    def _open_capture(self, config: FileSourceConfig) -> cv2.VideoCapture:
        source = Path(config.path).expanduser()
        LOGGER.info("Opening file ingestion source: %s", source)
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise TaskError(f"無法開啟指定影片：{source}")
        return capture

    def _get_drop_frames(self, config: FileSourceConfig) -> int:
        return max(config.drop_frames, 0)

    def _handle_failed_read(self, config: FileSourceConfig) -> bool:
        if not config.loop:
            raise TaskError("影片播放結束，且未啟用 EDGE_FILE_LOOP")
        if self._capture is None:
            raise TaskError("影片串流異常")
        LOGGER.info("File ingestion reached EOF，rewind to beginning")
        if not self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0):
            self._capture.release()
            self._capture = self._open_capture(config)
        return True

    def _get_capture_interval_seconds(self, config: FileSourceConfig, cycle_start: float) -> float:
        fps = config.fps or 0.0
        if fps <= 0:
            return 0.0
        elapsed = time.monotonic() - cycle_start
        return max(0.0, (1.0 / fps) - elapsed)
