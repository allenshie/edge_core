"""Shared helpers for edge pipeline task run methods."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from smart_workflow import TaskContext, TaskResult

from edge.runtime.duration_meter import DurationMeter
from edge.runtime.rate_meter import RateMeter
from edge.schema import FrameMeta
from edge.schema import StageStats


@dataclass(frozen=True)
class FrameTaskRuntime:
    frame_meta: FrameMeta | None
    is_new_frame: bool | None
    report_interval_seconds: float
    stale_threshold_seconds: float


class FrameTaskSupportMixin:
    @staticmethod
    def _health_report_interval(context: TaskContext, default: float = 5.0) -> float:
        return float(getattr(context.config, "health_report_interval_seconds", default) or default)

    @staticmethod
    def _health_stale_threshold(context: TaskContext, default: float = 0.0) -> float:
        return float(getattr(context.config, "health_stale_threshold_seconds", default) or default)

    @staticmethod
    def _frame_runtime(context: TaskContext) -> FrameTaskRuntime:
        frame_meta = context.get_resource("frame_meta")
        if not isinstance(frame_meta, FrameMeta):
            frame_meta = None
        is_new_frame = context.get_resource("pipeline_frame_is_new")
        if not isinstance(is_new_frame, bool):
            is_new_frame = None
        return FrameTaskRuntime(
            frame_meta=frame_meta,
            is_new_frame=is_new_frame,
            report_interval_seconds=FrameTaskSupportMixin._health_report_interval(context),
            stale_threshold_seconds=FrameTaskSupportMixin._health_stale_threshold(context),
        )

    @staticmethod
    def _build_task_result(payload: Mapping[str, Any], frame_meta: FrameMeta | None) -> TaskResult:
        result = dict(payload)
        if frame_meta is not None:
            result["frame_meta"] = frame_meta.to_dict()
        return TaskResult(payload=result)

    @staticmethod
    def _record_success(
        stats: StageStats,
        frame_meta: FrameMeta | None,
        *,
        latency_ms: float | None,
        worker_alive: bool,
        success_ts: datetime | None = None,
        queue_size: int | None = None,
    ) -> None:
        stats.record_success(
            session_id=frame_meta.session_id if isinstance(frame_meta, FrameMeta) else None,
            frame_seq=frame_meta.frame_seq if isinstance(frame_meta, FrameMeta) else None,
            capture_ts=frame_meta.capture_ts if isinstance(frame_meta, FrameMeta) else None,
            success_ts=success_ts or datetime.now(timezone.utc),
            latency_ms=latency_ms,
            worker_alive=worker_alive,
            queue_size=queue_size,
        )

    def _report_skip(
        self,
        context: TaskContext,
        *,
        stage: str,
        frame_meta: FrameMeta | None,
        note: str,
        reason: str,
        extra_fields: Mapping[str, Any] | None,
        report_interval_seconds: float,
        rate_meter: RateMeter | DurationMeter | None,
        rate_prefix: str | None,
        payload: Mapping[str, Any],
        skipped_resources: Mapping[str, Any] | None = None,
    ) -> TaskResult:
        self._health.report_skip(  # type: ignore[attr-defined]
            context,
            stage=stage,
            frame_meta=frame_meta,
            note=note,
            reason=reason,
            extra_fields=extra_fields,
            report_interval_seconds=report_interval_seconds,
            rate_meter=rate_meter,
            rate_prefix=rate_prefix,
        )
        for key, value in (skipped_resources or {}).items():
            context.set_resource(key, value)
        return self._build_task_result(payload, frame_meta)
