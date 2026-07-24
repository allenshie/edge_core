"""Inference engine implementations."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from smart_workflow import TaskContext, TaskError
from ultralytics import YOLO

from edge.config import ModelConfig
from edge.runtime.shutdown_summary import cleanup_record
from edge.schema import EdgeDetection

LOGGER = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parents[4]
PROJECT_ROOT = PACKAGE_ROOT.parent


@dataclass
class InferenceOutcome:
    """Standardized inference output returned by engines."""

    detections: List[EdgeDetection]
    models_run: List[str] = field(default_factory=list)
    models_reuse: List[str] = field(default_factory=list)


class BaseInferenceEngine:
    """Base class for custom inference engines."""

    def __init__(self, context: TaskContext | None = None) -> None:
        self._context = context

    def process(
        self,
        frame: Any,
        *,
        phase: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InferenceOutcome:
        raise NotImplementedError

    def close(self) -> list[dict[str, Any]]:
        return [
            cleanup_record(
                item="inference.engine",
                type="engine",
                state="done",
                ok=True,
                alive_before=False,
                alive_after=False,
                detail="no-op",
            )
        ]


class DefaultInferenceEngine(BaseInferenceEngine):
    """Legacy single-model YOLO engine."""

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._model = None
        self._model_config = context.config.model if context else None

    def process(
        self,
        frame: Any,
        *,
        phase: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InferenceOutcome:
        _ = (phase, metadata)
        if frame is None:
            LOGGER.warning("沒有待推理的 frame，略過")
            return InferenceOutcome(detections=[])
        self._ensure_model()
        model_cfg = self._model_config
        if model_cfg is None:
            raise TaskError("找不到模型設定")
        threshold = model_cfg.confidence_threshold
        tracker_cfg = self._resolve_tracker_config()
        track_kwargs = {"verbose": False}
        if tracker_cfg:
            track_kwargs["tracker"] = tracker_cfg
        results = self._model.track(frame, **track_kwargs)  # type: ignore[union-attr]
        detections = self._parse_results(results, threshold)
        return InferenceOutcome(detections=detections)

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        model_cfg = self._model_config
        if model_cfg is None:
            raise TaskError("找不到模型設定")
        try:
            model = YOLO(model_cfg.weights_path)
            if model_cfg.device:
                model.to(model_cfg.device)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error("無法載入 YOLO 模型 (%s): %s", model_cfg.weights_path, exc)
            raise TaskError(f"載入模型失敗: {exc}") from exc
        LOGGER.info("YOLO 模型載入成功：%s", model_cfg.weights_path)
        self._model = model
        self._model_config = model_cfg

    def _resolve_tracker_config(self) -> str | None:
        model_cfg: ModelConfig | None = self._model_config  # type: ignore[assignment]
        if model_cfg is None:
            return None
        try:
            return model_cfg.resolve_tracker_config(PROJECT_ROOT)
        except FileNotFoundError as exc:
            raise TaskError(str(exc)) from exc

    def _parse_results(self, results, threshold: float) -> List[EdgeDetection]:
        detections: List[EdgeDetection] = []
        if not results:
            return detections
        result = results[0]
        boxes = getattr(result, "boxes", None)
        names = getattr(result, "names", None) or getattr(getattr(result, "model", None), "names", {}) or {}
        if boxes is None:
            return detections

        xyxy_list = boxes.xyxy.cpu().tolist() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy.tolist()
        conf_list = boxes.conf.cpu().tolist() if hasattr(boxes.conf, "cpu") else boxes.conf.tolist()
        cls_list = boxes.cls.cpu().tolist() if hasattr(boxes.cls, "cpu") else boxes.cls.tolist()
        id_list = None
        if hasattr(boxes, "id") and boxes.id is not None:
            id_list = boxes.id.cpu().tolist() if hasattr(boxes.id, "cpu") else boxes.id.tolist()

        for idx, xyxy in enumerate(xyxy_list):
            conf = float(conf_list[idx]) if idx < len(conf_list) else 0.0
            if conf < threshold:
                continue
            bbox = [int(x) for x in xyxy]
            cls_id = int(cls_list[idx]) if idx < len(cls_list) and cls_list[idx] is not None else -1
            class_name = names.get(cls_id, str(cls_id))
            track_id = None
            if id_list and idx < len(id_list):
                try:
                    track_id = int(id_list[idx]) if id_list[idx] is not None else None
                except Exception:
                    track_id = None
            detections.append(
                EdgeDetection(
                    track_id=track_id,
                    class_name=class_name,
                    bbox=bbox,
                    bbox_confidence_score=conf,
                    score=conf,
                )
            )
        return detections
