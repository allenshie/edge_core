"""Common ingestion engine routines."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import cv2  # type: ignore[import]

from smart_workflow import TaskContext, TaskError
from edge.runtime.rate_meter import RateMeter
from edge.runtime.shutdown_summary import cleanup_record

LOGGER = logging.getLogger(__name__)


class BaseIngestionEngine:
    """Common routines for cv2.VideoCapture-based ingestion engines."""

    source_label = "ingestion"
    _first_frame_timeout_seconds = 5.0

    def __init__(self, context: TaskContext | None = None) -> None:
        self._capture: Optional[cv2.VideoCapture] = None
        self._cached_config: Any = None
        self._start_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frame_ready = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._latest_frame: Any = None
        self._latest_capture_ts: datetime | None = None
        self._latest_frame_seq: int = 0
        self._last_error: str | None = None
        self._started = False
        self._capture_rate = RateMeter()

    def start(self) -> None:
        with self._start_lock:
            if self.is_started():
                return
            config = self._cached_config
            if config is None:
                raise TaskError(f"{self.source_label} 設定尚未初始化")
            self._release_capture()
            LOGGER.info("starting %s ingestion capture thread", self.source_label)
            capture = self._open_capture(config)
            if not capture.isOpened():
                capture.release()
                raise TaskError(f"無法初始化 {self.source_label} capture")
            self._capture = capture
            self._stop_event.clear()
            self._frame_ready.clear()
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name=f"{self.source_label.capitalize()}IngestionCapture",
                args=(config,),
                daemon=True,
            )
            self._capture_thread.start()
            self._started = True

    def stop(self) -> list[dict[str, Any]]:
        alive_before = self.is_started()
        started = time.perf_counter()
        self._stop_event.set()
        self._release_capture()
        thread = self._capture_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        alive_after = self.is_started()
        if alive_before and alive_after:
            LOGGER.warning("%s ingestion capture thread still alive after stop", self.source_label)
        self._capture_thread = None
        self._started = False
        duration_ms = (time.perf_counter() - started) * 1000.0
        if not alive_before:
            return [
                cleanup_record(
                    item=f"{self.source_label}.capture",
                    type="thread",
                    state="skipped",
                    ok=True,
                    alive_before=False,
                    alive_after=False,
                    duration_ms=duration_ms,
                    detail="capture thread not running",
                )
            ]
        state = "done" if not alive_after else "timeout"
        detail = "capture thread joined" if not alive_after else "capture thread still alive after stop"
        return [
            cleanup_record(
                item=f"{self.source_label}.capture",
                type="thread",
                state=state,
                ok=not alive_after,
                alive_before=alive_before,
                alive_after=alive_after,
                duration_ms=duration_ms,
                detail=detail,
            )
        ]

    def close(self) -> list[dict[str, Any]]:
        return self.stop()

    def is_started(self) -> bool:
        thread = self._capture_thread
        return self._started and thread is not None and thread.is_alive()

    def health_snapshot(self) -> dict[str, Any]:
        _, capture_ts, frame_seq = self._snapshot_latest_frame()
        return {
            "worker_alive": self.is_started(),
            "frame_seq": frame_seq,
            "capture_ts": capture_ts,
            "last_error": self._last_error,
        }

    @property
    def capture_rate_meter(self) -> RateMeter:
        return self._capture_rate

    def fetch(self) -> dict[str, Any]:
        try:
            self.start()
            if not self._frame_ready.wait(timeout=self._first_frame_timeout_seconds):
                raise TaskError(f"{self.source_label} 無法取得影格")
            frame, capture_ts, frame_seq = self._snapshot_latest_frame()
            if frame is None:
                raise TaskError(f"{self.source_label} 無法取得影格")
            if capture_ts is None:
                capture_ts = datetime.now(timezone.utc)
            return {
                "frame": frame,
                "source": self.source_label,
                "capture_ts": capture_ts,
                "frame_seq": frame_seq,
            }
        except TaskError:
            self.stop()
            raise

    def _get_config(self, context: TaskContext):
        raise NotImplementedError

    def _open_capture(self, config) -> cv2.VideoCapture:
        raise NotImplementedError

    def _get_drop_frames(self, config) -> int:
        return 0

    def _handle_failed_read(self, config) -> bool:
        return False

    def _capture_loop(self, config: Any) -> None:
        try:
            while not self._stop_event.is_set():
                cycle_start = time.monotonic()
                latest_frame: Any = None
                latest_capture_ts: datetime | None = None
                frames_to_read = max(self._get_drop_frames(config), 0) + 1
                read_count = 0

                while read_count < frames_to_read and not self._stop_event.is_set():
                    capture = self._capture
                    ok, frame = capture.read() if capture is not None else (False, None)
                    if ok and frame is not None:
                        latest_frame = frame
                        latest_capture_ts = datetime.now(timezone.utc)
                        read_count += 1
                        continue

                    if self._stop_event.is_set():
                        break

                    try:
                        should_retry = self._handle_failed_read(config)
                    except TaskError as exc:
                        self._last_error = str(exc)
                        LOGGER.warning("%s ingestion capture stopped: %s", self.source_label, exc)
                        return

                    if not should_retry:
                        self._last_error = f"{self.source_label} 無法讀取影格"
                        LOGGER.warning("%s ingestion capture stopped: read failed", self.source_label)
                        return

                if latest_frame is not None and latest_capture_ts is not None:
                    self._store_latest_frame(latest_frame, latest_capture_ts)

                sleep_seconds = self._get_capture_interval_seconds(config, cycle_start)
                if sleep_seconds > 0:
                    self._stop_event.wait(timeout=sleep_seconds)
        finally:
            self._release_capture()
            self._started = False

    def _get_capture_interval_seconds(self, config: Any, cycle_start: float) -> float:
        _ = config
        _ = cycle_start
        return 0.0

    def _store_latest_frame(self, frame: Any, capture_ts: datetime) -> None:
        with self._frame_lock:
            self._latest_frame_seq += 1
            self._latest_frame = frame
            self._latest_capture_ts = capture_ts
            self._last_error = None
            self._frame_ready.set()
            self._capture_rate.mark(frame_seq=self._latest_frame_seq, ts=capture_ts)

    def _snapshot_latest_frame(self) -> tuple[Any, datetime | None, int]:
        with self._frame_lock:
            return self._latest_frame, self._latest_capture_ts, self._latest_frame_seq

    def _release_capture(self) -> None:
        if self._capture is not None:
            try:
                self._capture.release()
            finally:
                self._capture = None

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
