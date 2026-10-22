"""Capture worker: one thread and one frame source per connection."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from .sources import FrameSource, FrameSourceError, SourceFrame

LOGGER = logging.getLogger(__name__)

FrameCallback = Callable[[int, SourceFrame, datetime], None]
IntervalFn = Callable[[float], float]


class CaptureWorker:
    """服務單一次連線的 capture thread。

    source 只在這個 worker 的 thread 內開啟與關閉；stop event 屬於這個 worker，設定後不會再被清除。
    其他 thread 只能呼叫 request_stop()。
    """

    def __init__(
        self,
        *,
        generation: int,
        source: FrameSource,
        on_frame: FrameCallback,
        frames_per_cycle: int,
        interval_fn: IntervalFn,
        name: str,
    ) -> None:
        self.generation = generation
        self._source = source
        self._on_frame = on_frame
        self._frames_per_cycle = max(int(frames_per_cycle), 1)
        self._interval_fn = interval_fn
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.started_monotonic: float | None = None
        self.last_read_monotonic: float | None = None
        self.frames_delivered = 0
        self.exit_reason: str | None = None
        self.error: str | None = None
        self.stalled = False

    @property
    def name(self) -> str:
        return self._thread.name

    def start(self) -> None:
        self.started_monotonic = time.monotonic()
        self._thread.start()

    def request_stop(self) -> None:
        self._stop_event.set()
        try:
            self._source.abort()
        except Exception as exc:  # noqa: BLE001 - abort 失敗不能影響停止流程，只記錄
            LOGGER.warning("%s abort failed: %s", self.name, exc)

    def join(self, timeout: float) -> bool:
        """等待 worker 結束；回傳 True 代表已經結束。"""
        if self._thread is not threading.current_thread() and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            try:
                self._source.open()
            except FrameSourceError as exc:
                self.exit_reason = "open_failed"
                self.error = str(exc)
                return
            self._read_loop()
        except Exception as exc:  # noqa: BLE001 - 任何例外都轉成 worker 結束原因，交給 supervisor 重連
            self.exit_reason = "error"
            self.error = str(exc)
            LOGGER.exception("%s crashed", self.name)
        finally:
            try:
                self._source.close()
            except Exception as exc:  # noqa: BLE001 - close 失敗只記錄，worker 照常結束
                LOGGER.warning("%s close failed: %s", self.name, exc)

    def _read_loop(self) -> None:
        while not self._stop_event.is_set():
            cycle_start = time.monotonic()
            latest: SourceFrame | None = None
            latest_ts: datetime | None = None
            for _ in range(self._frames_per_cycle):
                if self._stop_event.is_set():
                    break
                frame = self._source.read()
                if frame is None:
                    self.exit_reason = "eof" if self._source.end_of_stream else "read_failed"
                    self.error = self._source.last_error
                    break
                self.last_read_monotonic = time.monotonic()
                latest = frame
                latest_ts = datetime.now(timezone.utc)

            if latest is not None and latest_ts is not None and not self._stop_event.is_set():
                self.frames_delivered += 1
                self._on_frame(self.generation, latest, latest_ts)
            if self.exit_reason is not None:
                return

            sleep_seconds = self._interval_fn(cycle_start)
            if sleep_seconds > 0:
                self._stop_event.wait(timeout=sleep_seconds)
        self.exit_reason = "stopped"
