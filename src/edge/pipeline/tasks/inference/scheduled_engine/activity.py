"""Forklift activity helpers for scheduled inference engine."""
from __future__ import annotations

from typing import Any, List

from edge.schema import EdgeDetection


def update_forklift_activity(
    engine: Any,
    task_name: str,
    results: List[EdgeDetection],
    now: float,
) -> None:
    if task_name not in engine._forklift_source_tasks:
        return

    last_seen = getattr(engine, "_forklift_last_seen_ts", None)
    prev_active = getattr(engine, "_forklift_active", False)

    if has_forklift(engine, results):
        last_seen = now
        engine._forklift_last_seen_ts = last_seen

    forklift_active = last_seen is not None and (now - last_seen) <= engine._forklift_active_hold
    forklift_idle = last_seen is None or (now - last_seen) >= engine._forklift_idle_seconds
    engine._forklift_active = forklift_active
    engine._forklift_idle = forklift_idle

    if prev_active and forklift_idle:
        engine._forklift_idle_since_ts = now
    if forklift_idle and getattr(engine, "_forklift_idle_since_ts", None) is None:
        engine._forklift_idle_since_ts = now


def forklift_is_idle(engine: Any, now: float) -> bool:
    last_seen = getattr(engine, "_forklift_last_seen_ts", None)
    if last_seen is None:
        return True
    return (now - last_seen) >= engine._forklift_idle_seconds


def last_run_before_idle(engine: Any, last_run: float) -> bool:
    idle_since = getattr(engine, "_forklift_idle_since_ts", None)
    if idle_since is None:
        return True
    return last_run < idle_since


def idle_for_at_least(engine: Any, seconds: float, now: float) -> bool:
    idle_since = getattr(engine, "_forklift_idle_since_ts", None)
    if idle_since is None:
        return True
    return (now - idle_since) >= seconds


def has_forklift(engine: Any, results: List[EdgeDetection]) -> bool:
    if not results:
        return False
    for det in results:
        class_name = (det.class_name or "").strip().lower()
        if class_name not in engine._forklift_class_names:
            continue
        score = det.score if det.score is not None else det.bbox_confidence_score
        if score is None:
            return True
        if float(score) >= engine._forklift_min_score:
            return True
    return False


__all__ = [
    "update_forklift_activity",
    "forklift_is_idle",
    "last_run_before_idle",
    "idle_for_at_least",
    "has_forklift",
]
