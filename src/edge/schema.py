"""資料交換模型。"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass
class EdgeDetection:
    track_id: int | None
    class_name: str
    score: float
    bbox: List[int]
    polygon: List[List[float]] | None = None
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "timestamp": self.timestamp.isoformat(),
            "detections": [det.to_dict() for det in self.detections],
        }

    @classmethod
    def now(cls, camera_id: str, detections: List[EdgeDetection]) -> "EdgeEvent":
        return cls(camera_id=camera_id, timestamp=datetime.now(timezone.utc), detections=detections)
