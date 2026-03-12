from __future__ import annotations

import logging

import cv2  # type: ignore[import]

from smart_workflow import TaskContext, TaskError

from edge.config import CameraSourceConfig

from .base import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)


class CameraIngestionEngine(BaseIngestionEngine):
    source_label = "camera"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._camera_config = context.config.ingestion.camera if context else None

    def _get_config(self, context: TaskContext) -> CameraSourceConfig:
        cfg = self._camera_config
        if cfg is None:
            ingestion = getattr(context.config, "ingestion", None)
            cfg = ingestion.camera if ingestion else None
        if cfg is None:
            raise TaskError("找不到 camera ingestion 設定")
        return cfg

    def _open_capture(self, config: CameraSourceConfig) -> cv2.VideoCapture:
        LOGGER.info("Opening camera device: %s", config.device)
        capture = cv2.VideoCapture(int(config.device))
        if config.frame_width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.frame_width)
        if config.frame_height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.frame_height)
        if config.fps:
            capture.set(cv2.CAP_PROP_FPS, config.fps)
        return capture

    def _get_drop_frames(self, config: CameraSourceConfig) -> int:
        return max(config.drop_frames, 0)
