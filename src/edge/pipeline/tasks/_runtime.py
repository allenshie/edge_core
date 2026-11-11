"""Shared helpers for edge pipeline task run methods."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from smart_workflow import TaskContext, TaskResult

from edge.schema import FrameMeta


@dataclass(frozen=True)
class FrameTaskRuntime:
    frame_meta: FrameMeta | None
    is_new_frame: bool | None
    report_interval_seconds: float


class FrameTaskSupportMixin:
    @staticmethod
    def _health_report_interval(context: TaskContext, default: float = 5.0) -> float:
        return float(getattr(context.config, "health_report_interval_seconds", default) or default)

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
        )

    def _set_counter_metrics(self, engine: Any, counters: Mapping[str, str]) -> None:
        """把 Engine 的計數器寫成這個節點的 metric（spec-edge.md 9.2）；Engine 沒有該屬性時不寫。"""
        for metric, attribute in counters.items():
            value = getattr(engine, attribute, None) if engine is not None else None
            if isinstance(value, int) and not isinstance(value, bool):
                self.set_metric(metric, value)  # type: ignore[attr-defined]

    @staticmethod
    def _build_task_result(
        payload: Mapping[str, Any],
        frame_meta: FrameMeta | None,
        *,
        status: str = "success",
        reason: str | None = None,
        message: str | None = None,
    ) -> TaskResult:
        result = dict(payload)
        if frame_meta is not None:
            result["frame_meta"] = frame_meta.to_dict()
        return TaskResult(status=status, payload=result, reason=reason, message=message)

    @staticmethod
    def _engine_result(outcome: Any) -> tuple[str, str | None, str | None]:
        """取出 Engine 結果的 result／reason／message；自訂 Engine 沒有這些欄位時視為 success。"""
        return (
            getattr(outcome, "result", None) or "success",
            getattr(outcome, "reason", None),
            getattr(outcome, "message", None),
        )

    def _skip_result(
        self,
        context: TaskContext,
        *,
        frame_meta: FrameMeta | None,
        reason: str,
        payload: Mapping[str, Any],
        skipped_resources: Mapping[str, Any] | None = None,
    ) -> TaskResult:
        """這一輪不處理：設定 skip 相關的 context 資源，回傳 skipped 的結果。"""
        for key, value in (skipped_resources or {}).items():
            context.set_resource(key, value)
        return self._build_task_result(payload, frame_meta, status="skipped", reason=reason)
