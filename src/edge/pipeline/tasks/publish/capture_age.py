"""publish 的 capture_age_ms_max：最近一個 EDGE_HEALTH_REPORT_INTERVAL_SEC 區間內的最大值。"""
from __future__ import annotations


class CaptureAgeWindow:
    """區間從第一筆開始計時，滿一個區間後下一筆開始新的區間。時間為 monotonic。"""

    def __init__(self) -> None:
        self._window_start: float | None = None
        self._max_ms = 0.0

    def add(self, age_ms: float, *, now: float, window_s: float) -> float:
        if self._window_start is None or now - self._window_start >= window_s:
            self._window_start = now
            self._max_ms = age_ms
        else:
            self._max_ms = max(self._max_ms, age_ms)
        return self._max_ms


__all__ = ["CaptureAgeWindow"]
