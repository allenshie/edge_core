"""Data models for scheduled inference engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from edge.schema import EdgeDetection


@dataclass
class ScheduledModelTask:
    name: str
    mode: str
    interval_seconds: float
    model: Any
    source_task: str | None = None
    min_interval_seconds: float = 0.0
    last_run: float | None = None
    run_once_completed: bool = False
    last_results: List[EdgeDetection] = field(default_factory=list)


__all__ = ["ScheduledModelTask"]
