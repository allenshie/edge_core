"""發布推理結果至整合端（委派至 engine）。"""
from __future__ import annotations

import importlib
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from .engine import BasePublishEngine, DefaultPublishEngine, MessagingPublishEngine


class PublishResultTask(BaseTask):
    name = "edge-publish"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine = self._load_engine(context)

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        detections: Sequence = context.get_resource("inference_output") or []
        outcome = self._engine.publish(context, detections)
        context.monitor.report_event(
            "edge_publish",
            detail=f"detections={outcome.published} status={outcome.status}",
            component=self.name,
        )
        return TaskResult(payload={"published": outcome.published, "status": outcome.status})

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

    def close(self, context: TaskContext) -> None:
        print("[PublishResultTask] close called")
        close_fn = getattr(self._engine, "close", None)
        if callable(close_fn):
            close_fn()
