from __future__ import annotations

from typing import Any, List, Tuple

import numpy as np

from edge.schema import EdgeDetection

from .yolo import BaseYoloModel


class YoloPoseModel(BaseYoloModel):
    """Reusable YOLO pose wrapper producing edge detections with keypoints."""

    def _postprocess_results(
        self,
        results: Any,
        frame: Any,
        metadata: Any,
        *,
        offset: Tuple[int, int] = (0, 0),
        start_track_id: int = 0,
    ) -> List[EdgeDetection]:
        _ = (frame, metadata)
        res = results[0]
        boxes = res.boxes
        keypoints = res.keypoints
        if boxes is None:
            return []

        names = res.names or getattr(self._model, "names", {})
        detections: List[EdgeDetection] = []
        track_id = start_track_id
        for idx in range(len(boxes)):
            xyxy = boxes.xyxy[idx].tolist()
            conf = float(boxes.conf[idx]) if boxes.conf is not None else 0.0
            cls_id = int(boxes.cls[idx]) if boxes.cls is not None else -1
            if isinstance(names, dict):
                class_name = names.get(cls_id, str(cls_id))
            else:
                class_name = str(names[cls_id]) if cls_id >= 0 else str(cls_id)

            kp_list: List[List[int]] = []
            kp_conf_score = 0.0
            if keypoints is not None:
                xy = keypoints.xy[idx].cpu().numpy() if hasattr(keypoints.xy, "cpu") else keypoints.xy[idx]
                confs = keypoints.conf[idx].cpu().numpy() if hasattr(keypoints.conf, "cpu") else keypoints.conf[idx]
                kp_list = self._format_keypoints(xy, offset=offset)
                if confs is not None and len(confs) > 0:
                    kp_conf_score = float(np.mean(confs[: len(kp_list)]))

            detections.append(
                EdgeDetection(
                    track_id=track_id,
                    class_name=class_name,
                    bbox=self._offset_bbox(xyxy, offset=offset),
                    bbox_confidence_score=conf,
                    score=conf,
                    keypoint=kp_list,
                    keypoint_confidence_score=kp_conf_score,
                )
            )
            track_id += 1
        return detections

    @staticmethod
    def _offset_bbox(xyxy: List[float], offset: Tuple[int, int]) -> List[int]:
        ox, oy = offset
        x1, y1, x2, y2 = [int(round(v)) for v in xyxy]
        return [x1 + ox, y1 + oy, x2 + ox, y2 + oy]

    @staticmethod
    def _format_keypoints(xy, offset: Tuple[int, int]) -> List[List[int]]:
        ox, oy = offset
        return [[int(pt[0]) + ox, int(pt[1]) + oy] for pt in xy][:8]
