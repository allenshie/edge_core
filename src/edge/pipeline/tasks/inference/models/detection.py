from __future__ import annotations

import logging
import math
import time
from typing import Any, List

from edge.schema import EdgeDetection

from .yolo import BaseYoloModel

LOGGER = logging.getLogger(__name__)


class YoloDetectionModel(BaseYoloModel):
    """Reusable YOLO detection wrapper supporting predict or track mode."""

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
        self._infer_mode = self.config.get("infer_mode", "track")
        self._tracker = self.config.get("tracker")
        self._tracked_classes = self.config.get("tracked_classes")
        self._track_reset_interval_seconds = self._parse_track_reset_interval(
            self.config.get("track_reset_interval_seconds", 0)
        )
        self._last_track_reset_monotonic: float | None = None
        self._track_reset_count = 0

    @staticmethod
    def _parse_track_reset_interval(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            interval = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("track_reset_interval_seconds must be a non-negative number") from exc
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("track_reset_interval_seconds must be a non-negative finite number")
        return interval

    def _reset_trackers_if_due(self) -> None:
        interval = self._track_reset_interval_seconds
        if interval <= 0:
            return

        now = time.monotonic()
        if self._last_track_reset_monotonic is None:
            self._last_track_reset_monotonic = now
            return
        if now - self._last_track_reset_monotonic < interval:
            return

        predictor = getattr(self._model, "predictor", None)
        trackers = getattr(predictor, "trackers", None)
        if not trackers:
            self._last_track_reset_monotonic = now
            return

        reset_count = 0
        for tracker in trackers:
            reset = getattr(tracker, "reset", None)
            if callable(reset):
                reset()
                reset_count += 1

        self._last_track_reset_monotonic = now
        if reset_count:
            self._track_reset_count += 1
            LOGGER.info(
                "reset %d tracker(s) for model=%s after %.1f seconds (reset_count=%d)",
                reset_count,
                self.name,
                interval,
                self._track_reset_count,
            )

    def _predict_raw(self, frame: Any, metadata: Any):
        _ = metadata
        if self._model is None or frame is None:
            return []
        kwargs = self._build_predict_kwargs()
        if self._infer_mode == "track":
            self._reset_trackers_if_due()
            if self._tracker:
                return self._model.track(frame, persist=True, tracker=self._tracker, **kwargs)
            return self._model.track(frame, persist=True, **kwargs)
        return self._model.predict(frame, **kwargs)

    def _should_keep_track_id(self, cls_id: int) -> bool:
        return self._tracked_classes is None or cls_id in self._tracked_classes

    def _postprocess_results(self, results: Any, frame: Any, metadata: Any) -> List[EdgeDetection]:
        _ = (frame, metadata)
        res = results[0]
        boxes = res.boxes
        if boxes is None:
            return []

        names = res.names or getattr(self._model, "names", {})
        detections: List[EdgeDetection] = []
        for idx in range(len(boxes)):
            xyxy = [int(pt) for pt in boxes.xyxy[idx].tolist()]
            conf = float(boxes.conf[idx]) if boxes.conf is not None else 0.0
            cls_id = int(boxes.cls[idx]) if boxes.cls is not None else -1
            track_id: int | None = None
            if self._infer_mode == "track" and boxes.id is not None and self._should_keep_track_id(cls_id):
                try:
                    raw_track_id = boxes.id[idx]
                    track_id = int(raw_track_id) if raw_track_id is not None else None
                except Exception:
                    track_id = None
            if isinstance(names, dict):
                class_name = names.get(cls_id, str(cls_id))
            else:
                class_name = str(names[cls_id]) if cls_id >= 0 else str(cls_id)
            detections.append(
                EdgeDetection(
                    track_id=track_id,
                    class_name=class_name,
                    bbox=[int(round(v)) for v in xyxy],
                    bbox_confidence_score=conf,
                    score=conf,
                )
            )
        return detections
