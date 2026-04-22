"""資料交換模型。"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Sequence


@dataclass(frozen=True)
class FrameMeta:
    session_id: str
    frame_seq: int
    capture_ts: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "frame_seq": self.frame_seq,
            "capture_ts": self.capture_ts.isoformat(),
        }

    @classmethod
    def now(cls, session_id: str, frame_seq: int) -> "FrameMeta":
        return cls(session_id=session_id, frame_seq=frame_seq, capture_ts=datetime.now(timezone.utc))

    def age_seconds(self, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - self.capture_ts).total_seconds())


@dataclass
class StageStats:
    task_name: str
    health_state: str = "ok"
    session_id: str | None = None
    last_frame_seq: int | None = None
    last_capture_ts: datetime | None = None
    last_success_ts: datetime | None = None
    last_latency_ms: float | None = None
    last_warning_ts: datetime | None = None
    last_warning_reason: str | None = None
    last_error_ts: datetime | None = None
    last_error_reason: str | None = None
    warning_count: int = 0
    error_count: int = 0
    worker_alive: bool = True
    queue_size: int | None = None
    last_reported_state: str | None = None
    _last_report_monotonic: float = field(default=0.0, repr=False)

    def record_success(
        self,
        *,
        session_id: str | None = None,
        frame_seq: int | None = None,
        capture_ts: datetime | None = None,
        success_ts: datetime | None = None,
        latency_ms: float | None = None,
        worker_alive: bool | None = None,
        queue_size: int | None = None,
    ) -> None:
        self.health_state = "ok"
        self.last_success_ts = success_ts or datetime.now(timezone.utc)
        if session_id is not None:
            self.session_id = session_id
        if frame_seq is not None:
            self.last_frame_seq = frame_seq
        if capture_ts is not None:
            self.last_capture_ts = capture_ts
        if latency_ms is not None:
            self.last_latency_ms = latency_ms
        if worker_alive is not None:
            self.worker_alive = worker_alive
        if queue_size is not None:
            self.queue_size = queue_size

    def record_warning(
        self,
        reason: str | None = None,
        *,
        warning_ts: datetime | None = None,
        worker_alive: bool | None = None,
        queue_size: int | None = None,
    ) -> None:
        self.health_state = "degraded"
        self.warning_count += 1
        self.last_warning_ts = warning_ts or datetime.now(timezone.utc)
        self.last_warning_reason = reason
        if worker_alive is not None:
            self.worker_alive = worker_alive
        if queue_size is not None:
            self.queue_size = queue_size

    def record_error(
        self,
        reason: str | None = None,
        *,
        error_ts: datetime | None = None,
        worker_alive: bool | None = None,
        queue_size: int | None = None,
    ) -> None:
        self.health_state = "error"
        self.error_count += 1
        self.last_error_ts = error_ts or datetime.now(timezone.utc)
        self.last_error_reason = reason
        if worker_alive is not None:
            self.worker_alive = worker_alive
        if queue_size is not None:
            self.queue_size = queue_size

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        return {
            "task": self.task_name,
            "state": self.health_state,
            "session_id": self.session_id,
            "frame_seq": self.last_frame_seq,
            "capture_ts": self.last_capture_ts,
            "capture_age_s": self.capture_age_seconds(now),
            "age_s": self.last_success_age_seconds(now),
            "latency_ms": self.last_latency_ms,
            "warn": self.warning_count,
            "err": self.error_count,
            "worker_alive": self.worker_alive,
            "queue_size": self.queue_size,
            "last_warning_reason": self.last_warning_reason,
            "last_error_reason": self.last_error_reason,
        }

    def last_success_age_seconds(self, now: datetime | None = None) -> float | None:
        if self.last_success_ts is None:
            return None
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - self.last_success_ts).total_seconds())

    def capture_age_seconds(self, now: datetime | None = None) -> float | None:
        if self.last_capture_ts is None:
            return None
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - self.last_capture_ts).total_seconds())

    def should_report(self, report_interval_seconds: float, now_monotonic: float | None = None) -> bool:
        if report_interval_seconds <= 0:
            return False
        current = now_monotonic or time.monotonic()
        if self._last_report_monotonic <= 0:
            return True
        return (current - self._last_report_monotonic) >= report_interval_seconds

    def mark_reported(self, now_monotonic: float | None = None) -> None:
        current = now_monotonic or time.monotonic()
        self._last_report_monotonic = current
        self.last_reported_state = self.health_state


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
