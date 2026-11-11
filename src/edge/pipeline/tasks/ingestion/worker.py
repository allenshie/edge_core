"""Capture worker: one thread and one frame source per connection."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

from .sources import FrameSource, FrameSourceError, SourceFrame

LOGGER = logging.getLogger(__name__)

FrameCallback = Callable[[int, SourceFrame, datetime], None]
IntervalFn = Callable[[float], float]
ReadBeginFn = Callable[[int], None]
# (generation, outcome, error)；outcome 為 frame、eof、read_failed、open_failed、error 之一。
ReadEndFn = Callable[[int, str, "str | None"], None]


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
        recent_reads: int = 1,
        on_read_begin: ReadBeginFn | None = None,
        on_read_end: ReadEndFn | None = None,
    ) -> None:
        self.generation = generation
        self._source = source
        self._on_frame = on_frame
        self._frames_per_cycle = max(int(frames_per_cycle), 1)
        self._interval_fn = interval_fn
        # 健康狀態用的通知，只觀察讀取，不影響讀取流程。
        self._on_read_begin = on_read_begin
        self._on_read_end = on_read_end
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.started_monotonic: float | None = None
        # 最近 recent_reads 次成功讀取的時間，supervisor 用來判斷時間視窗內的畫面數。
        self._recent_reads: deque[float] = deque(maxlen=max(int(recent_reads), 1))
        self.frames_delivered = 0
        self.exit_reason: str | None = None
        self.error: str | None = None
        # 由 supervisor 設定；worker thread 結束時會改寫 error，所以 stall 原因另外存放。
        self.stall_reason: str | None = None

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

    def read_times(self) -> list[float]:
        """最近幾次成功讀取的 monotonic 時間，由舊到新。"""
        return list(self._recent_reads)

    def _run(self) -> None:
        try:
            try:
                self._source.open()
            except FrameSourceError as exc:
                self.exit_reason = "open_failed"
                self.error = str(exc)
                self._notify_end("open_failed", self.error)
                return
            self._read_loop()
        except Exception as exc:  # noqa: BLE001 - 任何例外都轉成 worker 結束原因，交給 supervisor 重連
            self.exit_reason = "error"
            self.error = str(exc)
            LOGGER.exception("%s crashed", self.name)
            self._notify_end("error", self.error)
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
                self._notify_begin()
                frame = self._source.read()
                if frame is None:
                    self.exit_reason = "eof" if self._source.end_of_stream else "read_failed"
                    self.error = self._source.last_error
                    self._notify_end(self.exit_reason, self.error)
                    break
                self._notify_end("frame", None)
                self._recent_reads.append(time.monotonic())
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

    def _notify_begin(self) -> None:
        if self._on_read_begin is None:
            return
        try:
            self._on_read_begin(self.generation)
        except Exception:  # noqa: BLE001 - 健康通知失敗不能影響讀取
            LOGGER.debug("%s read-begin hook failed", self.name, exc_info=True)

    def _notify_end(self, outcome: str, error: str | None) -> None:
        if self._on_read_end is None:
            return
        try:
            self._on_read_end(self.generation, outcome, error)
        except Exception:  # noqa: BLE001 - 健康通知失敗不能影響讀取
            LOGGER.debug("%s read-end hook failed", self.name, exc_info=True)
