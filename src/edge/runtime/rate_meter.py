"""Thread-safe rolling rate meter for stage throughput."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any


class RateMeter:
    def __init__(self, min_elapsed_seconds: float = 1.0) -> None:
        self._lock = threading.Lock()
        self._min_elapsed_seconds = max(0.0, float(min_elapsed_seconds))
        self._window_start_monotonic: float | None = None
        self._window_event_count = 0
        self._last_complete_fps: float | None = None
        self._last_event_ts: datetime | None = None
        self._last_event_frame_seq: int | None = None

    def mark(self, frame_seq: int | None = None, ts: datetime | None = None) -> None:
        now = time.monotonic()
        event_ts = ts or datetime.now(timezone.utc)
        with self._lock:
            if self._window_event_count == 0 or self._window_start_monotonic is None:
                self._window_start_monotonic = now
            self._window_event_count += 1
            self._last_event_ts = event_ts
            if frame_seq is not None:
                self._last_event_frame_seq = frame_seq

    def fps(self, now_monotonic: float | None = None) -> float | None:
        current = time.monotonic() if now_monotonic is None else now_monotonic
        with self._lock:
            if self._window_event_count <= 0:
                return None
            if self._window_start_monotonic is None:
                return None
            elapsed = current - self._window_start_monotonic
            if elapsed <= 0 or elapsed < self._min_elapsed_seconds:
                return self._last_complete_fps
            return self._window_event_count / elapsed

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._window_event_count

    def snapshot(self, prefix: str) -> dict[str, Any]:
        fps = self.fps()
        return {f"{prefix}_fps": fps}

    def mark_reported(self, now_monotonic: float | None = None) -> None:
        current = time.monotonic() if now_monotonic is None else now_monotonic
        with self._lock:
            if self._window_start_monotonic is not None and self._window_event_count > 0:
                elapsed = current - self._window_start_monotonic
                if elapsed > 0 and elapsed >= self._min_elapsed_seconds:
                    self._last_complete_fps = self._window_event_count / elapsed
            self._window_start_monotonic = current
            self._window_event_count = 0
