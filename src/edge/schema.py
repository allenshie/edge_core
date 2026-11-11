"""資料交換模型。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass(frozen=True)
class FrameMeta:
    session_id: str
    frame_seq: int
    capture_ts: datetime
    # 讀到畫面時的 monotonic 時間，只供健康回報計算 capture_age_ms；不送給 App，也不參與比較。
    capture_monotonic: float | None = field(default=None, compare=False)

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

        def append_track(
            camera_id: Any,
            local_id: Any,
            global_id: Any,
            class_name: Any,
        ) -> None:
            camera_key = str(camera_id).strip()
            parsed_local_id = _coerce_matching_local_id(local_id)
            if not camera_key or parsed_local_id is None:
                return
            grouped.setdefault(camera_key, []).append(
                MatchingResultTrack(
                    local_id=parsed_local_id,
                    global_id=global_id,
                    class_name=str(class_name or "unknown"),
                )
            )

        camera_matches = raw_payload.get("camera_matches") or {}
        if isinstance(camera_matches, Mapping):
            for camera_id, tracks in camera_matches.items():
                if isinstance(tracks, Sequence) and not isinstance(tracks, (str, bytes)):
                    for item in tracks:
                        if not isinstance(item, Mapping):
                            continue
                        append_track(
                            camera_id,
                            item.get("local_id"),
                            item.get("global_id"),
                            item.get("class_name"),
                        )

        # Matching broadcast v2 stores locals under each global object's
        # ``matched_locals`` list instead of the legacy camera-keyed mapping.
        matching_objects = raw_payload.get("objects") or []
        if isinstance(matching_objects, Sequence) and not isinstance(matching_objects, (str, bytes)):
            for global_object in matching_objects:
                if not isinstance(global_object, Mapping):
                    continue
                parent_global_id = global_object.get("global_id")
                matched_locals = global_object.get("matched_locals") or []
                if not isinstance(matched_locals, Sequence) or isinstance(matched_locals, (str, bytes)):
                    continue
                for local_object in matched_locals:
                    if not isinstance(local_object, Mapping):
                        continue
                    local_global_id = local_object.get("global_id")
                    if local_global_id is None:
                        local_global_id = parent_global_id
                    append_track(
                        local_object.get("camera_id"),
                        local_object.get("local_id"),
                        local_global_id,
                        local_object.get("class_name") or global_object.get("class_name"),
                    )

        for tracks in grouped.values():
            tracks.sort(key=lambda track: track.local_id)

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
