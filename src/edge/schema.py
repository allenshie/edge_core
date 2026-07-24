"""資料交換模型。"""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


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
    session_id: str | None = None
    frame_seq: int | None = None
    capture_ts: datetime | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "session_id": self.session_id,
            "frame_seq": self.frame_seq,
            "capture_ts": self.capture_ts.isoformat() if self.capture_ts is not None else None,
            "timestamp": self.timestamp.isoformat(),
            "detections": [det.to_dict() for det in self.detections],
            "models": list(self.models),
        }

    @classmethod
    def now(
        cls,
        camera_id: str,
        detections: List[EdgeDetection],
        models: List[str] | None = None,
        frame_meta: FrameMeta | None = None,
    ) -> "EdgeEvent":
        return cls(
            camera_id=camera_id,
            timestamp=datetime.now(timezone.utc),
            detections=detections,
            models=models or [],
            session_id=frame_meta.session_id if frame_meta is not None else None,
            frame_seq=frame_meta.frame_seq if frame_meta is not None else None,
            capture_ts=frame_meta.capture_ts if frame_meta is not None else None,
        )


@dataclass
class MatchingResultTrack:
    local_id: int
    global_id: Any = None
    class_name: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "local_id": self.local_id,
            "global_id": self.global_id,
        }
        if self.class_name:
            payload["class_name"] = self.class_name
        return payload


@dataclass
class MatchingResultPayload:
    schema_version: int = 1
    message_type: str = "matching_result"
    generated_at: str = ""
    camera_matches: Dict[str, List[MatchingResultTrack]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "MatchingResultPayload":
        raw_payload = dict(payload or {})
        grouped: dict[str, list[MatchingResultTrack]] = {}
        camera_matches = raw_payload.get("camera_matches") or {}
        if isinstance(camera_matches, Mapping):
            for camera_id, tracks in camera_matches.items():
                camera_key = str(camera_id).strip()
                if not camera_key:
                    continue
                parsed_tracks: list[MatchingResultTrack] = []
                if isinstance(tracks, Sequence) and not isinstance(tracks, (str, bytes)):
                    for item in tracks:
                        if not isinstance(item, Mapping):
                            continue
                        local_id = _coerce_matching_local_id(item.get("local_id"))
                        if local_id is None:
                            continue
                        parsed_tracks.append(
                            MatchingResultTrack(
                                local_id=local_id,
                                global_id=item.get("global_id"),
                                class_name=str(item.get("class_name") or "unknown"),
                            )
                        )
                if parsed_tracks:
                    parsed_tracks.sort(key=lambda track: track.local_id)
                    grouped[camera_key] = parsed_tracks
        return cls(
            schema_version=_coerce_optional_int(raw_payload.get("schema_version"), default=1),
            message_type=str(raw_payload.get("message_type") or "matching_result"),
            generated_at=str(raw_payload.get("generated_at") or _format_timestamp(datetime.now(timezone.utc))),
            camera_matches=dict(sorted(grouped.items(), key=lambda item: item[0])),
        )

    def selected_camera_tracks(self, camera_id: str) -> list[MatchingResultTrack]:
        return list(self.camera_matches.get(camera_id, []))

    def local_to_global_mapping(self, camera_id: str) -> Dict[int, Any]:
        return {
            track.local_id: track.global_id
            for track in self.selected_camera_tracks(camera_id)
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "message_type": self.message_type,
            "generated_at": self.generated_at,
            "camera_matches": {
                camera_id: [track.to_dict() for track in tracks]
                for camera_id, tracks in self.camera_matches.items()
            },
        }

    def to_local_snapshot(
        self,
        camera_id: str,
        *,
        result_version: int = 0,
        enabled: bool = True,
        subscribed: bool = True,
        reason: str | None = None,
    ) -> Dict[str, Any]:
        selected_tracks = [track.to_dict() for track in self.camera_matches.get(camera_id, [])]
        local_to_global = self.local_to_global_mapping(camera_id)
        return {
            "enabled": enabled,
            "subscribed": subscribed,
            "camera_id": camera_id,
            "schema_version": self.schema_version,
            "message_type": self.message_type,
            "generated_at": self.generated_at,
            "result_version": result_version,
            "camera_matches": {camera_id: selected_tracks} if selected_tracks else {},
            "local_to_global": local_to_global,
            "matches": len(local_to_global),
            "payload": {
                "schema_version": self.schema_version,
                "message_type": self.message_type,
                "generated_at": self.generated_at,
                "camera_matches": {camera_id: selected_tracks} if selected_tracks else {},
            },
            "reason": reason,
        }


def _coerce_matching_local_id(value: Any) -> int | None:
    try:
        local_id = int(value)
    except (TypeError, ValueError):
        return None
    return local_id if local_id >= 0 else None


def _coerce_optional_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()
