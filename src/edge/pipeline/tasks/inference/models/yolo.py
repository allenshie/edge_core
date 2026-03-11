from __future__ import annotations

from abc import abstractmethod
from typing import Any, List

from edge.schema import EdgeDetection

from .base import BaseEdgeModel


class BaseYoloModel(BaseEdgeModel):
    """Shared Ultralytics YOLO bootstrap and inference flow."""

    def __init__(
        self,
        name: str,
        weights_path: str | None = None,
        label: str | None = None,
        device: str | None = None,
        *,
        config_loader: callable | None = None,
    ) -> None:
        super().__init__(
            name=name,
            weights_path=weights_path,
            label=label,
            device=device,
            config_loader=config_loader,
        )
        self._model = None
        self._conf = self.config.get("conf")
        self._iou = self.config.get("iou")
        self._classes = self.config.get("classes")
        self._verbose = self.config.get("verbose")
        self._imgsz = self.config.get("imgsz")
        if self.weights_path:
            self._model = self._load_model()

    def _load_model(self):
        from ultralytics import YOLO

        return YOLO(str(self.weights_path))

    def _build_predict_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.device:
            kwargs["device"] = self.device
        if self._conf is not None:
            kwargs["conf"] = self._conf
        if self._iou is not None:
            kwargs["iou"] = self._iou
        if self._classes is not None:
            kwargs["classes"] = self._classes
        if self._verbose is not None:
            kwargs["verbose"] = self._verbose
        if self._imgsz is not None:
            kwargs["imgsz"] = self._imgsz
        return kwargs

    def _prepare_frame(self, frame: Any, metadata: Any) -> Any:
        _ = metadata
        return frame

    def _predict_raw(self, frame: Any, metadata: Any):
        _ = metadata
        if self._model is None or frame is None:
            return []
        return self._model.predict(frame, **self._build_predict_kwargs())

    @abstractmethod
    def _postprocess_results(self, results: Any, frame: Any, metadata: Any) -> List[EdgeDetection]:
        """Convert raw YOLO results into edge detections."""

    def run(self, frame: Any, metadata: Any) -> List[EdgeDetection]:
        prepared = self._prepare_frame(frame, metadata)
        if prepared is None or self._model is None:
            return []
        results = self._predict_raw(prepared, metadata)
        if not results:
            return []
        return self._postprocess_results(results, prepared, metadata)
