"""Execution policy helpers for scheduled inference engine."""
from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


def should_execute(task: Any, now: float, context: Any, engine: Any) -> bool:
    mode = task.mode
    if mode == "every_frame":
        return True
    if mode == "interval":
        return task.last_run is None or (now - task.last_run) >= task.interval_seconds
    if mode == "interval_when_idle":
        if not engine._forklift_is_idle(context, now):
            return False
        if task.last_run is None or engine._last_run_before_idle(context, task.last_run):
            return engine._idle_for_at_least(context, task.min_interval_seconds, now)
        if task.interval_seconds <= 0:
            return False
        return (now - task.last_run) >= task.interval_seconds
    if mode == "run_once_after_switch":
        return not task.run_once_completed
    if mode == "replay_last":
        return task.last_run is None or (now - task.last_run) >= task.interval_seconds

    LOGGER.warning("unknown scheduled mode=%s for task=%s", mode, task.name)
    return False


__all__ = ["should_execute"]
