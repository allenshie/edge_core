"""Exponential reconnect backoff for ingestion workers."""
from __future__ import annotations


class ReconnectBackoff:
    def __init__(self, base_seconds: float, max_seconds: float) -> None:
        self._base_seconds = max(float(base_seconds), 0.0)
        self._max_seconds = max(float(max_seconds), self._base_seconds)
        self._failures = 0

    def next_delay(self) -> float:
        delay = min(self._max_seconds, self._base_seconds * (2**self._failures))
        self._failures += 1
        return delay

    def reset(self) -> None:
        self._failures = 0
