"""Typed health snapshot contracts shared by tasks and summaries."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypedDict, runtime_checkable

from smart_workflow import TaskContext


class HealthSummaryRow(TypedDict):
    stage: str
    state: str
    session_id: str | None
    frame_seq: int | None
    fps: float | None
    age_s: float | None
    alive: bool
    note: str


class HealthSummaryMetrics(HealthSummaryRow, total=False):
    capture_age_s: float | None
    pipeline_fps: float | None
    capture_fps: float | None
    infer_fps: float | None
    stream_output_fps: float | None
    stream_unique_fps: float | None
    publish_fps: float | None


@runtime_checkable
class HealthSnapshotProvider(Protocol):
    def snapshot_health(self, context: TaskContext | None = None) -> Mapping[str, Any]:
        ...
