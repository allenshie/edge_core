"""Shared cv2-based ingestion task helpers."""
from __future__ import annotations

import logging
from typing import Any, Optional

import cv2  # type: ignore[import]

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

LOGGER = logging.getLogger(__name__)


class BaseIngestionTask(BaseTask):
    """Common routines for cv2 VideoCapture ingestion tasks."""

    source_label = "ingestion"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._capture: Optional[cv2.VideoCapture] = None
        self._cached_config: Any = None

    def _get_config(self, context: TaskContext):
        raise NotImplementedError

    def _open_capture(self, config) -> cv2.VideoCapture:
        raise NotImplementedError

    def _get_drop_frames(self, config) -> int:
        return 0

    def _handle_failed_read(self, config) -> bool:
        """Handle read failure. Return True to retry without raising."""

        return False

    def _ensure_capture(self, context: TaskContext) -> Any:
        config = self._cached_config or self._get_config(context)
        if self._capture is not None and self._capture.isOpened():
            return config
        if self._capture is not None:
            self._capture.release()
        self._cached_config = config
        LOGGER.info("initializing %s capture", self.source_label)
        capture = self._open_capture(config)
        if not capture.isOpened():
            raise TaskError(f"無法初始化 {self.source_label} capture")
        self._capture = capture
        return config

    def _read_latest_frame(self, config) -> Optional[Any]:
        frames_to_grab = max(self._get_drop_frames(config), 0) + 1
        grabbed = 0
        latest_frame = None
        while grabbed < frames_to_grab:
            ok, frame = self._capture.read() if self._capture else (False, None)
            if ok and frame is not None:
                grabbed += 1
                latest_frame = frame
                continue
            if not self._handle_failed_read(config):
                return None
        return latest_frame

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        try:
            config = self._ensure_capture(context)
            frame = self._read_latest_frame(config)
            if frame is None:
                raise TaskError(f"{self.source_label} 無法取得影格")
            context.set_resource("decoded_frame", frame)
            context.set_resource("decoded_frame_path", None)
            return TaskResult(payload={"source": self.source_label})
        except TaskError as exc:
            LOGGER.warning("%s ingestion failed: %s", self.source_label, exc)
            if self._capture is not None:
                self._capture.release()
                self._capture = None
            raise

    def __del__(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
