"""Matching result receiver task."""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timezone
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.runtime.task_health import TaskHealthReporter
from edge.schema import FrameMeta, StageStats

from .constants import (
    MATCHING_RESULT_LOCAL_TO_GLOBAL_RESOURCE,
    MATCHING_RESULT_SNAPSHOT_RESOURCE,
    MATCHING_RESULT_SUBSCRIBED_RESOURCE,
)
from .engine import BaseMatchingResultEngine, DefaultMatchingResultEngine, MatchingResultState


class MatchingResultTask(FrameTaskSupportMixin, BaseTask):
    """Keep the latest matching broadcast snapshot in task resources."""

    name = "edge-matching-result"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._enabled = self._resolve_enabled(context)
        self._engine: BaseMatchingResultEngine | None = self._load_engine(context) if self._enabled else None
        self._stats = StageStats(task_name="matching")
        self._last_snapshot: dict[str, object] | None = None
        self._health = TaskHealthReporter(self._stats)
        if not self._enabled:
            self._health.report_execution(
                context,
                stage="matching",
                health_state="inactive",
                frame_meta=None,
                note="matching_result_disabled",
                reason="matching_result_disabled",
                extra_fields={
                    "subscribed": False,
                    "matches": 0,
                    "enabled": False,
                    "skipped": True,
                    "skip_reason": "matching_result_disabled",
                },
                report_interval_seconds=float(
                    getattr(context.config, "health_report_interval_seconds", 5.0) if context is not None else 5.0
                ),
                emit=False,
            )

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        runtime = self._frame_runtime(context)
        frame_meta = runtime.frame_meta
        if not self._enabled:
            return self._handle_disabled(context, runtime, frame_meta)
        if self._engine is None:
            raise TaskError("MatchingResultTask 必須在初始化時提供 TaskContext")
        started_at = time.perf_counter()
        snapshot = self._engine.snapshot()
        local_snapshot = self._build_local_snapshot(snapshot)
        self._store_snapshot_resources(context, local_snapshot)
        self._last_snapshot = local_snapshot
        self._record_success(
            self._stats,
            frame_meta,
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            worker_alive=snapshot.subscribed,
            success_ts=datetime.now(timezone.utc),
        )
        self._health.report_execution(
            context,
            stage="matching",
            health_state="ok" if snapshot.subscribed else "degraded",
            frame_meta=frame_meta,
            note=f"camera={snapshot.camera_id} matches={len(local_snapshot['local_to_global'])} subscribed={snapshot.subscribed}",
            reason=snapshot.reason,
            extra_fields={
                "enabled": snapshot.enabled,
                "subscribed": snapshot.subscribed,
                "matches": len(local_snapshot["local_to_global"]),
                "camera_id": snapshot.camera_id,
                "result_version": snapshot.result_version,
            },
            report_interval_seconds=runtime.report_interval_seconds,
            worker_alive=snapshot.subscribed,
        )
        return self._build_task_result(
            {
                "matching_result": local_snapshot,
                "enabled": snapshot.enabled,
                "subscribed": snapshot.subscribed,
                "matches": len(local_snapshot["local_to_global"]),
            },
            frame_meta,
        )

    def snapshot_health(self, context: TaskContext | None = None) -> dict:
        if self._last_snapshot is not None:
            return self._health.snapshot(self._last_snapshot)
        return self._health.snapshot()

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        return self.snapshot_health(context)

    def close(self, context: TaskContext) -> list[dict]:
        _ = context
        if self._engine is None:
            return []
        close_fn = getattr(self._engine, "close", None)
        if callable(close_fn):
            result = close_fn()
            if isinstance(result, list):
                return result
            if result is not None:
                return [result]
        return []

    def _handle_disabled(
        self,
        context: TaskContext,
        runtime,
        frame_meta: FrameMeta | None,
    ) -> TaskResult:
        summary_fields = {
            "enabled": False,
            "subscribed": False,
            "matches": 0,
            "camera_id": getattr(context.config.camera, "camera_id", "unknown") if context and context.config else "unknown",
            "skipped": True,
            "reason": "matching_result_disabled",
            "skip_reason": "matching_result_disabled",
        }
        self._last_snapshot = None
        self._store_disabled_resources(context)
        return self._report_skip(
            context,
            stage="matching",
            frame_meta=frame_meta,
            note="skipped=matching_result_disabled matches=0 subscribed=false",
            reason="matching_result_disabled",
            extra_fields=summary_fields,
            report_interval_seconds=runtime.report_interval_seconds,
            rate_meter=None,
            rate_prefix=None,
            skipped_resources={
                MATCHING_RESULT_SUBSCRIBED_RESOURCE: False,
                MATCHING_RESULT_LOCAL_TO_GLOBAL_RESOURCE: {},
            },
            payload={
                "matching_result": None,
                "enabled": False,
                "subscribed": False,
                "matches": 0,
                "reason": "matching_result_disabled",
                "skipped": True,
            },
        )

    def _store_snapshot_resources(self, context: TaskContext, snapshot: dict[str, object]) -> None:
        context.set_resource(MATCHING_RESULT_SNAPSHOT_RESOURCE, snapshot)
        context.set_resource(
            MATCHING_RESULT_LOCAL_TO_GLOBAL_RESOURCE,
            dict(snapshot.get("local_to_global") or {}),
        )
        context.set_resource(MATCHING_RESULT_SUBSCRIBED_RESOURCE, bool(snapshot.get("subscribed")))

    def _store_disabled_resources(self, context: TaskContext) -> None:
        context.set_resource(MATCHING_RESULT_SNAPSHOT_RESOURCE, None)
        context.set_resource(MATCHING_RESULT_LOCAL_TO_GLOBAL_RESOURCE, {})
        context.set_resource(MATCHING_RESULT_SUBSCRIBED_RESOURCE, False)

    def _build_local_snapshot(self, snapshot: MatchingResultState) -> dict[str, object]:
        selected_tracks = [
            dict(track)
            for track in snapshot.camera_matches.get(snapshot.camera_id, [])
        ]
        local_to_global = dict(snapshot.local_to_global)
        return {
            "enabled": snapshot.enabled,
            "subscribed": snapshot.subscribed,
            "camera_id": snapshot.camera_id,
            "schema_version": snapshot.schema_version,
            "message_type": snapshot.message_type,
            "generated_at": snapshot.generated_at,
            "result_version": snapshot.result_version,
            "camera_matches": {snapshot.camera_id: selected_tracks} if selected_tracks else {},
            "local_to_global": local_to_global,
            "matches": len(local_to_global),
            "payload": {
                "schema_version": snapshot.schema_version,
                "message_type": snapshot.message_type,
                "generated_at": snapshot.generated_at,
                "camera_matches": {snapshot.camera_id: selected_tracks} if selected_tracks else {},
            },
            "reason": snapshot.reason,
        }

    def _resolve_enabled(self, context: TaskContext | None) -> bool:
        if context is None:
            return False
        matching_cfg = getattr(context.config, "matching_result", None)
        return bool(getattr(matching_cfg, "enabled", False))

    def _load_engine(self, context: TaskContext | None) -> BaseMatchingResultEngine:
        engine_path = getattr(context.config, "matching_result_engine_class", None) if context else None
        if not engine_path:
            return DefaultMatchingResultEngine(context=context)
        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

    def _import_engine(self, path: str) -> Type[BaseMatchingResultEngine]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 MatchingResult Engine：{path}")
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if engine_cls is None or not issubclass(engine_cls, BaseMatchingResultEngine):
            raise TaskError(f"{class_name} 必須繼承 BaseMatchingResultEngine")
        return engine_cls
