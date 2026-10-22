from __future__ import annotations

import logging

import cv2  # type: ignore[import]
from smart_workflow import TaskContext

from edge.config import CameraSourceConfig

from ..sources import FrameSource, OpenCvFrameSource
from .base import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)


class CameraIngestionEngine(BaseIngestionEngine):
    source_label = "camera"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._cached_config = context.config.ingestion.camera if context else None

    def _build_source(self, config: CameraSourceConfig) -> FrameSource:
        LOGGER.info("Opening camera device: %s", config.device)
        properties: dict[int, float] = {}
        if config.frame_width:
            properties[cv2.CAP_PROP_FRAME_WIDTH] = config.frame_width
        if config.frame_height:
            properties[cv2.CAP_PROP_FRAME_HEIGHT] = config.frame_height
        if config.fps:
            properties[cv2.CAP_PROP_FPS] = config.fps
        return OpenCvFrameSource(int(config.device), properties=properties)

    def _get_drop_frames(self, config: CameraSourceConfig) -> int:
        return max(config.drop_frames, 0)
