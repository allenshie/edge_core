"""Rate-limit repetitive log records from noisy third-party loggers."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

MQTT_LOGGER_NAME = "smart_messaging_core.protocols.mqtt"


class RateLimitedLogFilter(logging.Filter):
    """同一種訊息在時間窗內只放行第一筆，窗口過後的下一筆附上略過筆數。

    以 (logger 名稱, level, 未格式化的 msg) 當 key，所以只有參數不同（例如 rc 值）的訊息視為同一種。
    """

    def __init__(self, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self.window_seconds = max(float(window_seconds), 0.0)
        self._clock = clock
        self._lock = threading.Lock()
        # key -> [窗口起點, 窗口內略過筆數]
        self._windows: dict[tuple[str, int, object], list[float | int]] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if self.window_seconds <= 0:
            return True
        key = (record.name, record.levelno, record.msg)
        now = self._clock()
        with self._lock:
            window = self._windows.get(key)
            if window is not None and now - float(window[0]) < self.window_seconds:
                window[1] = int(window[1]) + 1
                return False
            suppressed = int(window[1]) if window is not None else 0
            self._windows[key] = [now, 0]
        if suppressed:
            record.msg = f"{record.msg}（過去 {self.window_seconds:g}s 略過 {suppressed} 筆）"
        return True


def install_mqtt_log_throttle() -> RateLimitedLogFilter | None:
    """在 smart_messaging_core 的 MQTT logger 掛上節流 filter；重複呼叫只會掛一次。"""
    window_seconds = float(os.environ.get("EDGE_MQTT_LOG_THROTTLE_SECONDS", "60") or 0)
    if window_seconds <= 0:
        return None
    logger = logging.getLogger(MQTT_LOGGER_NAME)
    for existing in logger.filters:
        if isinstance(existing, RateLimitedLogFilter):
            return existing
    log_filter = RateLimitedLogFilter(window_seconds=window_seconds)
    logger.addFilter(log_filter)
    return log_filter
