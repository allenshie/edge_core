"""Inference task delegating to pluggable engines."""
from __future__ import annotations

import importlib
import time
from typing import Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from .engine import BaseInferenceEngine, DefaultInferenceEngine, render_inference_frame


class InferenceTask(BaseTask):
    name = "edge-inference"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine = self._load_engine(context)

    def run(self, context: TaskContext) -> TaskResult:
        start = time.time()
        detections = self._engine.process(context)
        context.set_resource("inference_output", detections)
        self._maybe_render_visualization(context, detections)
        context.logger.info(
            "[InferenceTask] 推理耗時 %.3fs，偵測 %d 個物件",
            time.time() - start,
            len(detections),
        )
        return TaskResult(payload={"detections": detections})

    def _load_engine(self, context: TaskContext | None) -> BaseInferenceEngine:
        engine_path = getattr(context.config, "inference_engine_class", None) if context else None
        if not engine_path:
            return DefaultInferenceEngine(context=context)
        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

    def _import_engine(self, path: str) -> Type[BaseInferenceEngine]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 Inference Engine：{path}")
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if engine_cls is None or not issubclass(engine_cls, BaseInferenceEngine):
            raise TaskError(f"{class_name} 必須繼承 BaseInferenceEngine")
        return engine_cls

    def _maybe_render_visualization(self, context: TaskContext, detections) -> None:
        model_cfg = context.config.model
        visual_cfg = context.config.visualization
        if not model_cfg.visualize or not visual_cfg.enabled:
            return
        frame = context.get_resource("decoded_frame")
        if frame is None:
            context.logger.warning("沒有待視覺化的 frame，略過")
            return
        frame_path = context.get_resource("decoded_frame_path")
        output_path = render_inference_frame(frame, detections, frame_path, visual_cfg)
        context.set_resource("frame_path", output_path)
