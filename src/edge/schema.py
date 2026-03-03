"""資料交換模型。"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence


@dataclass
class EdgeDetection:
    track_id: int | None
    class_name: str
    bbox: List[int]
    bbox_confidence_score: float
    score: float | None = None
    polygon: List[List[int]] = field(default_factory=list)
    polygon_confidence_score: float = 0.0
    keypoint: List[List[int]] = field(default_factory=list)
    keypoint_confidence_score: float = 0.0
    state: str | Sequence[str] | None = None
    keypoints: List[List[float]] | None = None
    category: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EdgeEvent:
    camera_id: str
    timestamp: datetime
    detections: List[EdgeDetection]
    models: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "timestamp": self.timestamp.isoformat(),
            "detections": [det.to_dict() for det in self.detections],
            "models": list(self.models),
        }

    @classmethod
    def now(
        cls, camera_id: str, detections: List[EdgeDetection], models: List[str] | None = None
    ) -> "EdgeEvent":
        return cls(
            camera_id=camera_id,
            timestamp=datetime.now(timezone.utc),
            detections=detections,
            models=models or [],
        )
