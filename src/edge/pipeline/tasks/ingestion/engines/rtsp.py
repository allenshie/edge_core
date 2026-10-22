from __future__ import annotations

import logging

import cv2  # type: ignore[import]
from smart_workflow import TaskContext

from edge.config import RtspConfig

from ..sources import FrameSource, OpenCvFrameSource
from .base import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)


class RtspIngestionEngine(BaseIngestionEngine):
    source_label = "rtsp"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._cached_config = context.config.ingestion.rtsp if context else None

    def _build_source(self, config: RtspConfig) -> FrameSource:
        LOGGER.info("Connecting to RTSP source: %s", config.url)
        properties: dict[int, float] = {}
        if config.frame_width:
            properties[cv2.CAP_PROP_FRAME_WIDTH] = config.frame_width
        if config.frame_height:
            properties[cv2.CAP_PROP_FRAME_HEIGHT] = config.frame_height
        return OpenCvFrameSource(
            config.url,
            open_timeout_seconds=self._worker_config.open_timeout_seconds,
            read_timeout_seconds=self._worker_config.read_timeout_seconds,
            properties=properties,
        )

    def _get_drop_frames(self, config: RtspConfig) -> int:
        return max(config.drop_frames, 0)

    def _reconnect_base_seconds(self, config: RtspConfig) -> float:
        return max(getattr(config, "reconnect_seconds", 0.0) or 0.0, 0.0)
