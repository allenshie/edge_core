"""Shared types for streaming task/engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from edge.schema import EdgeDetection


@dataclass
class StreamPacket:
    frame: Any
    detections: Sequence[EdgeDetection]
    phase: str
    timestamp: float


@dataclass
class StreamingStatus:
    queue_size: int
    dropped_frames: int
    processed_frames: int
    stream_active: bool
    should_stream: bool
    phase: str
    enabled: bool
    last_error: str | None = None
    state: str = "inactive"
    reconnect_count: int = 0
    write_failures: int = 0
    no_frame_seconds: float = 0.0
    since_last_write_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "queue_size": self.queue_size,
            "dropped_frames": self.dropped_frames,
            "processed_frames": self.processed_frames,
            "stream_active": self.stream_active,
            "should_stream": self.should_stream,
            "phase": self.phase,
            "enabled": self.enabled,
            "last_error": self.last_error,
            "state": self.state,
            "reconnect_count": self.reconnect_count,
            "write_failures": self.write_failures,
            "no_frame_seconds": self.no_frame_seconds,
            "since_last_write_seconds": self.since_last_write_seconds,
        }
