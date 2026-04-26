"""發布推理結果至整合端（委派至 engine）。"""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timezone
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.pipeline.tasks._runtime import FrameTaskSupportMixin
from edge.runtime.rate_meter import RateMeter
from edge.runtime.duration_meter import DurationMeter
from edge.runtime.task_health import TaskHealthReporter
from edge.schema import FrameMeta, StageStats

from .engine import BasePublishEngine, MessagingPublishEngine


class PublishResultTask(FrameTaskSupportMixin, BaseTask):
    name = "edge-publish"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._enabled = self._resolve_enabled(context)
        self._engine: BasePublishEngine | None = self._load_engine(context) if self._enabled else None
        self._stats = StageStats(task_name="publish")
        self._last_outcome = None
        self._last_frame_meta: FrameMeta | None = None
        self._publish_latency = DurationMeter()
        self._publish_rate = RateMeter()
        self._health = TaskHealthReporter(self._stats)
        if not self._enabled:
            self._health.report_execution(
                context,
                stage="publish",
                health_state="inactive",
                frame_meta=None,
                note="publish_disabled",
                reason="publish_disabled",
                extra_fields={
                    "published": 0,
                    "status": "skipped",
                    "skipped": True,
                    "reason": "publish_disabled",
                    "skip_reason": "publish_disabled",
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
        detections: Sequence = context.get_resource("inference_output") or []
        if runtime.is_new_frame is False:
            return self._handle_stale_frame(context, runtime, frame_meta)
        return self._process_publish(context, runtime, detections, frame_meta)

    def _handle_disabled(
        self,
        context: TaskContext,
        runtime,
        frame_meta: FrameMeta | None,
    ) -> TaskResult:
        summary_fields = {
            "published": 0,
            "status": "skipped",
            "skipped": True,
            "reason": "publish_disabled",
            "skip_reason": "publish_disabled",
        }
        self._last_outcome = None
        self._last_frame_meta = frame_meta
        return self._report_skip(
            context,
            stage="publish",
            frame_meta=frame_meta,
            note="skipped=publish_disabled published=0 status=skipped",
            reason="publish_disabled",
            extra_fields=summary_fields,
            report_interval_seconds=runtime.report_interval_seconds,
            rate_meter=self._publish_rate,
            rate_prefix="publish",
            skipped_resources={
                "publish_skipped": True,
                "publish_skip_reason": "publish_disabled",
            },
            payload={"published": 0, "status": None, "skipped": True, "reason": "publish_disabled"},
        )

    def _handle_stale_frame(
        self,
        context: TaskContext,
        runtime,
        frame_meta: FrameMeta | None,
    ) -> TaskResult:
        summary_fields = {
            "published": 0,
            "status": "skipped",
            "skipped": True,
            "reason": "stale_frame",
            "skip_reason": "stale_frame",
        }
        self._last_outcome = None
        self._last_frame_meta = frame_meta
        return self._report_skip(
            context,
            stage="publish",
            frame_meta=frame_meta,
            note="skipped=stale_frame published=0 status=skipped",
            reason="stale_frame",
            extra_fields=summary_fields,
            report_interval_seconds=runtime.report_interval_seconds,
            rate_meter=self._publish_rate,
            rate_prefix="publish",
            skipped_resources={
                "publish_skipped": True,
                "publish_skip_reason": "stale_frame",
            },
            payload={"published": 0, "status": None, "skipped": True},
        )

    def _process_publish(
        self,
        context: TaskContext,
        runtime,
        detections: Sequence,
        frame_meta: FrameMeta | None,
    ) -> TaskResult:
        models_run = list(context.get_resource("inference_models_run") or [])
        models_reuse = list(context.get_resource("inference_models_reuse") or [])
        started_at = time.perf_counter()
        outcome = None
        try:
            outcome = self._engine.publish(
                detections,
                models_run=models_run,
                models_reuse=models_reuse,
            )
        finally:
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            self._publish_latency.mark(elapsed_ms)
        if outcome is None:
            raise TaskError("publish engine did not return an outcome")
        self._publish_rate.mark(frame_seq=frame_meta.frame_seq if isinstance(frame_meta, FrameMeta) else None)
        self._last_outcome = outcome
        self._last_frame_meta = frame_meta
        context.set_resource("publish_skipped", False)
        context.set_resource("publish_skip_reason", None)
        self._record_success(
            self._stats,
            frame_meta,
            latency_ms=elapsed_ms,
            worker_alive=True,
            success_ts=datetime.now(timezone.utc),
        )
        self._health.report_publish(
            context,
            frame_meta=frame_meta,
            outcome=outcome,
            publish_rate_meter=self._publish_rate,
            stale_threshold_seconds=runtime.stale_threshold_seconds,
            report_interval_seconds=runtime.report_interval_seconds,
        )
        return self._build_task_result({"published": outcome.published, "status": outcome.status}, frame_meta)

    def snapshot_health(self, context: TaskContext | None = None) -> dict:
        _ = context
        if self._engine is None:
            return self._health.snapshot()
        return self._health.snapshot_publish(
            frame_meta=self._last_frame_meta,
            outcome=self._last_outcome,
            publish_rate_meter=self._publish_rate,
        )

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        return self.snapshot_health(context)

    def _load_engine(self, context: TaskContext | None) -> BasePublishEngine:
        engine_path = getattr(context.config, "publish_engine_class", None) if context else None
        if not engine_path:
            return MessagingPublishEngine(context=context)
        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

    def _resolve_enabled(self, context: TaskContext | None) -> bool:
        if context is None:
            return False
        publish_cfg = getattr(context.config, "publish", None)
        return bool(getattr(publish_cfg, "enabled", True))

    def _import_engine(self, path: str) -> Type[BasePublishEngine]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 Publish Engine：{path}")
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if engine_cls is None or not issubclass(engine_cls, BasePublishEngine):
            raise TaskError(f"{class_name} 必須繼承 BasePublishEngine")
        return engine_cls

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
