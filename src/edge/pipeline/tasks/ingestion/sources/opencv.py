"""cv2.VideoCapture-based frame sources."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import cv2  # type: ignore[import]

from .base import FrameSource, FrameSourceError, SourceFrame, redact_url

LOGGER = logging.getLogger(__name__)


class OpenCvFrameSource(FrameSource):
    """URL 或本機裝置來源；URL 會以 FFmpeg backend 開啟並套用 open / read timeout。"""

    def __init__(
        self,
        target: str | int,
        *,
        open_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        properties: Mapping[int, float] | None = None,
    ) -> None:
        super().__init__()
        self._target = target
        self._open_timeout_seconds = open_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._properties = dict(properties or {})
        self._capture: cv2.VideoCapture | None = None

    def open(self) -> None:
        capture = self._create_capture()
        for prop, value in self._properties.items():
            capture.set(prop, value)
        if not capture.isOpened():
            capture.release()
            raise FrameSourceError(self._open_error_message())
        self._capture = capture

    def read(self) -> SourceFrame | None:
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        if ok and frame is not None:
            return SourceFrame(image=frame)
        self.last_error = f"無法讀取影格：{redact_url(str(self._target))}"
        return None

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()

    def _create_capture(self) -> cv2.VideoCapture:
        if isinstance(self._target, str) and (self._open_timeout_seconds or self._read_timeout_seconds):
            params: list[int] = []
            if self._open_timeout_seconds:
                params += [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self._open_timeout_seconds * 1000)]
            if self._read_timeout_seconds:
                params += [cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self._read_timeout_seconds * 1000)]
            return cv2.VideoCapture(self._target, cv2.CAP_FFMPEG, params)
        return cv2.VideoCapture(self._target)

    def _open_error_message(self) -> str:
        return f"無法開啟來源：{redact_url(str(self._target))}"


class OpenCvFileSource(OpenCvFrameSource):
    """影片檔來源；loop 時在 EOF 後回到開頭繼續讀。"""

    def __init__(self, path: str | Path, *, loop: bool) -> None:
        self._path = Path(path).expanduser()
        super().__init__(str(self._path))
        self._loop = loop

    def read(self) -> SourceFrame | None:
        frame = super().read()
        if frame is not None:
            return frame
        if not self._loop:
            self.end_of_stream = True
            self.last_error = "影片播放結束，且未啟用 EDGE_FILE_LOOP"
            return None
        LOGGER.info("File ingestion reached EOF，rewind to beginning")
        if self._capture is None or not self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0):
            self.close()
            self.open()
        return super().read()

    def _open_error_message(self) -> str:
        return f"無法開啟指定影片：{self._path}"
