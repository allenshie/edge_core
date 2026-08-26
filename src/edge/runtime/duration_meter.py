"""Thread-safe aggregate duration meter for stage latency-based throughput."""
from __future__ import annotations

import math
import threading
from typing import Any


class DurationMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sample_count = 0
        self._total_ms = 0.0

    def mark(self, duration_ms: float | None) -> None:
        if duration_ms is None:
            return
        value = float(duration_ms)
        if not math.isfinite(value) or value <= 0:
            return
        with self._lock:
            self._sample_count += 1
            self._total_ms += value

    def avg_ms(self) -> float | None:
        with self._lock:
            if self._sample_count <= 0:
                return None
            return self._total_ms / self._sample_count

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._sample_count

    def fps(self, now_monotonic: float | None = None) -> float | None:
        _ = now_monotonic
        with self._lock:
            if self._sample_count <= 0 or self._total_ms <= 0:
                return None
            return (1000.0 * self._sample_count) / self._total_ms

    def snapshot(self, prefix: str) -> dict[str, Any]:
        return {f"{prefix}_fps": self.fps()}

    def summary(self, prefix: str) -> dict[str, Any]:
        with self._lock:
            average_ms = (
                self._total_ms / self._sample_count
                if self._sample_count > 0
                else None
            )
            return {
                f"{prefix}_samples": self._sample_count,
                f"{prefix}_avg_ms": average_ms,
            }

    def mark_reported(self, now_monotonic: float | None = None) -> None:
        _ = now_monotonic
        with self._lock:
            self._sample_count = 0
            self._total_ms = 0.0
