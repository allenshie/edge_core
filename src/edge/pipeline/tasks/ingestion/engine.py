"""Ingestion engine implementations."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

import cv2  # type: ignore[import]

from smart_workflow import TaskContext, TaskError

from edge.config import FileSourceConfig, RtspConfig

LOGGER = logging.getLogger(__name__)


class BaseIngestionEngine:
    """Common routines for cv2.VideoCapture-based ingestion engines."""

    source_label = "ingestion"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._capture: Optional[cv2.VideoCapture] = None
        self._cached_config: Any = None

    def fetch(self, context: TaskContext) -> dict:
        try:
            config = self._ensure_capture(context)
            frame = self._read_latest_frame(config)
            if frame is None:
                raise TaskError(f"{self.source_label} 無法取得影格")
            context.set_resource("decoded_frame", frame)
            context.set_resource("decoded_frame_path", None)
            return {"source": self.source_label}
        except TaskError:
            if self._capture is not None:
                self._capture.release()
                self._capture = None
            raise

    def _get_config(self, context: TaskContext):
        raise NotImplementedError

    def _open_capture(self, config) -> cv2.VideoCapture:
        raise NotImplementedError

    def _get_drop_frames(self, config) -> int:
        return 0

    def _handle_failed_read(self, config) -> bool:
        return False

    def _ensure_capture(self, context: TaskContext):
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

    def __del__(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class FileIngestionEngine(BaseIngestionEngine):
    source_label = "file"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._file_config = context.config.ingestion.file if context else None

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


class RtspIngestionEngine(BaseIngestionEngine):
    source_label = "rtsp"

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._rtsp_config = context.config.ingestion.rtsp if context else None

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
            time.sleep(reconnect)
        try:
            new_capture = self._open_capture(config)
        except TaskError:
            return False
        if not new_capture.isOpened():
            return False
        self._capture = new_capture
        LOGGER.info("RTSP 重新連線成功：%s", config.url)
        return True
