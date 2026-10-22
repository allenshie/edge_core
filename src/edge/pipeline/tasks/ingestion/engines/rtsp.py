from __future__ import annotations

import logging
import shlex
import shutil

import cv2  # type: ignore[import]
from smart_workflow import TaskContext, TaskError

from edge.config import RtspConfig

from ..sources import FfmpegFrameSource, FrameSource, OpenCvFrameSource
from .base import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)

DECODERS = ("ffmpeg", "opencv")


class RtspIngestionEngine(BaseIngestionEngine):
    source_label = "rtsp"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._cached_config = context.config.ingestion.rtsp if context else None

    def _validate_config(self, config: RtspConfig) -> None:
        if config.decoder not in DECODERS:
            raise TaskError(f"不支援的 EDGE_RTSP_DECODER：{config.decoder}（可用：{', '.join(DECODERS)}）")
        if config.decoder == "ffmpeg" and shutil.which(config.ffmpeg_bin) is None:
            raise TaskError(f"找不到 ffmpeg 執行檔：{config.ffmpeg_bin}；可設定 EDGE_RTSP_DECODER=opencv 改用 OpenCV")

    def _build_source(self, config: RtspConfig) -> FrameSource:
        LOGGER.info("Connecting to RTSP source (%s): %s", config.decoder, config.url)
        if config.decoder == "ffmpeg":
            return FfmpegFrameSource(
                config.url,
                binary=config.ffmpeg_bin,
                transport=config.transport,
                open_timeout_seconds=self._worker_config.open_timeout_seconds,
                read_timeout_seconds=self._worker_config.read_timeout_seconds,
                width=config.frame_width,
                height=config.frame_height,
                input_args=shlex.split(config.ffmpeg_input_args),
            )
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
