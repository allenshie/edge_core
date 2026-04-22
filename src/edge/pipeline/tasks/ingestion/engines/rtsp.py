from __future__ import annotations

import logging

import cv2  # type: ignore[import]

from smart_workflow import TaskContext, TaskError

from edge.config import RtspConfig

from .base import BaseIngestionEngine

LOGGER = logging.getLogger(__name__)


class RtspIngestionEngine(BaseIngestionEngine):
    source_label = "rtsp"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._rtsp_config = context.config.ingestion.rtsp if context else None
        self._cached_config = self._rtsp_config

    def _get_config(self, context: TaskContext) -> RtspConfig:
        if self._rtsp_config:
            return self._rtsp_config
        ingestion = getattr(context.config, "ingestion", None)
        if ingestion and ingestion.rtsp:
            return ingestion.rtsp
        cfg = getattr(context.config, "rtsp", None)
        if cfg:
            return cfg
        raise TaskError("找不到 RTSP 設定")

    def _open_capture(self, config: RtspConfig) -> cv2.VideoCapture:
        LOGGER.info("Connecting to RTSP source: %s", config.url)
        capture = cv2.VideoCapture(config.url)
        if config.frame_width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.frame_width)
        if config.frame_height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.frame_height)
        return capture

    def _get_drop_frames(self, config: RtspConfig) -> int:
        return max(config.drop_frames, 0)

    def _handle_failed_read(self, config: RtspConfig) -> bool:
        LOGGER.warning("RTSP 讀取失敗，嘗試重新連線：%s", config.url)
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        reconnect = max(getattr(config, "reconnect_seconds", 0.0) or 0.0, 0.0)
        if reconnect:
            LOGGER.info("%.2f 秒後嘗試重新連線 RTSP", reconnect)
            # stop() 時以 Event.wait 取代 time.sleep，讓關閉流程可以即時中斷重連等待。
            if self._stop_event.wait(timeout=reconnect):
                return False
        if self._stop_event.is_set():
            return False
        try:
            new_capture = self._open_capture(config)
        except TaskError:
            return False
        if not new_capture.isOpened():
            return False
        self._capture = new_capture
        LOGGER.info("RTSP 重新連線成功：%s", config.url)
        return True
