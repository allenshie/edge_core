"""發布推理結果至整合端（委派至 engine）。"""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timezone
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.runtime.task_health import TaskHealthReporter
from edge.schema import FrameMeta, StageStats

from .engine import BasePublishEngine, MessagingPublishEngine


class PublishResultTask(BaseTask):
    name = "edge-publish"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine = self._load_engine(context)
        self._stats = StageStats(task_name="publish")
        self._last_outcome = None
        self._health = TaskHealthReporter(self._stats)

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        detections: Sequence = context.get_resource("inference_output") or []
        frame_meta = context.get_resource("frame_meta")
        models_run = list(context.get_resource("inference_models_run") or [])
        models_reuse = list(context.get_resource("inference_models_reuse") or [])
        started_at = time.perf_counter()
        outcome = self._engine.publish(
            detections,
            models_run=models_run,
            models_reuse=models_reuse,
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        self._last_outcome = outcome
        self._stats.record_success(
            session_id=frame_meta.session_id if isinstance(frame_meta, FrameMeta) else None,
            frame_seq=frame_meta.frame_seq if isinstance(frame_meta, FrameMeta) else None,
            capture_ts=frame_meta.capture_ts if isinstance(frame_meta, FrameMeta) else None,
            success_ts=datetime.now(timezone.utc),
            latency_ms=elapsed_ms,
            worker_alive=True,
        )
        report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
        stale_threshold = float(getattr(context.config, "health_stale_threshold_seconds", 0.0) or 0.0)
        self._health.report_publish(
            context,
            frame_meta=frame_meta if isinstance(frame_meta, FrameMeta) else None,
            outcome=outcome,
            stale_threshold_seconds=stale_threshold,
            report_interval_seconds=report_interval,
        )
        frame_meta_payload = frame_meta.to_dict() if isinstance(frame_meta, FrameMeta) else None
        payload = {"published": outcome.published, "status": outcome.status}
        if frame_meta_payload is not None:
            payload["frame_meta"] = frame_meta_payload
        return TaskResult(payload=payload)

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        _ = context
        return self._health.snapshot_publish(frame_meta=None, outcome=self._last_outcome)

    def _load_engine(self, context: TaskContext | None) -> BasePublishEngine:
        engine_path = getattr(context.config, "publish_engine_class", None) if context else None
        if not engine_path:
            return MessagingPublishEngine(context=context)
        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

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
        close_fn = getattr(self._engine, "close", None)
        if callable(close_fn):
            result = close_fn()
            if isinstance(result, list):
                return result
            if result is not None:
                return [result]
        return []
