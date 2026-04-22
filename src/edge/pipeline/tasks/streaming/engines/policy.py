"""Streaming phase and health policy helpers."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from ..types import StreamingStatus

LOGGER = logging.getLogger(__name__)

STATE_INACTIVE = "inactive"
STATE_IDLE = "idle"
STATE_STREAMING = "streaming"
STATE_DEGRADED = "degraded"


def resolve_phase(phase: str | None) -> str:
    if not phase:
        phase = (
            os.environ.get("EDGE_MODE_DEFAULT")
            or os.environ.get("EDGE_DEMO_DEFAULT")
            or os.environ.get("EDGE_DEMO_DEFAULT_PHASE")
            or "working_stage_1"
        )
    return str(phase)


def load_streaming_schedule(context: Any | None) -> Dict[str, bool]:
    resource_root = _resolve_resource_root()
    schedule_path = _get_schedule_path(resource_root)
    if not schedule_path.exists():
        return {}
    try:
        data = json.loads(schedule_path.read_text())
    except json.JSONDecodeError:
        LOGGER.warning("invalid schedule json, skip streaming policy: %s", schedule_path)
        return {}

    policy: Dict[str, bool] = {}
    for phase, definition in data.items():
        if isinstance(definition, dict):
            streaming = definition.get("streaming", {})
            if isinstance(streaming, dict) and "enabled" in streaming:
                policy[phase] = bool(streaming["enabled"])
    if policy:
        LOGGER.info("loaded streaming phase policy from %s: %s", schedule_path, policy)
    return policy


def should_stream_for_phase(enabled: bool, streaming_enabled_by_phase: Dict[str, bool], phase: str) -> bool:
    if not enabled:
        return False
    phase_enabled = streaming_enabled_by_phase.get(phase)
    if phase_enabled is None:
        return enabled
    return enabled and phase_enabled


def activate_stream(engine: Any, phase: str) -> bool:
    if engine._stream_active:
        return True
    if not engine._url:
        engine._last_error = "EDGE_STREAMING_URL is empty"
        engine._state = STATE_DEGRADED
        LOGGER.warning("streaming requested but url is empty (phase=%s)", phase)
        return False
    engine._stream_active = True
    engine._state = STATE_STREAMING
    LOGGER.info("streaming activated (phase=%s)", phase)
    return True


def deactivate_stream(engine: Any, phase: str, reason: str) -> None:
    if not engine._stream_active:
        if reason == "no_frame_timeout":
            engine._state = STATE_IDLE
        return
    engine._stream_active = False
    engine._state = STATE_IDLE if reason == "no_frame_timeout" else STATE_INACTIVE
    engine._clear_latest_packet()
    engine._ffmpeg.close()
    LOGGER.info("streaming deactivated (phase=%s reason=%s)", phase, reason)


def build_status(engine: Any, phase: str, should_stream: bool, now: float) -> StreamingStatus:
    no_frame_seconds = (now - engine._last_frame_ts) if getattr(engine, "_last_frame_ts", None) else 0.0
    since_last_write = (now - engine._last_write_ts) if getattr(engine, "_last_write_ts", None) else 0.0
    return StreamingStatus(
        # 單一 thread 版不再使用 queue；保留 queue_size 只是為了讓既有 summary 欄位相容。
        queue_size=0,
        dropped_frames=engine._dropped_frames,
        processed_frames=engine._processed_frames,
        stream_active=engine._stream_active,
        should_stream=should_stream,
        phase=phase,
        enabled=engine._enabled,
        last_error=engine._last_error,
        state=engine._state,
        reconnect_count=engine._reconnect_count,
        write_failures=engine._write_failures,
        no_frame_seconds=no_frame_seconds,
        since_last_write_seconds=since_last_write,
        ffmpeg_alive=engine._ffmpeg.is_alive(),
    )


def log_health(engine: Any, force: bool, phase: str, should_stream: bool) -> None:
    now = time.time()
    report_interval = getattr(engine, "_health_report_interval_seconds", 5.0)
    if not force and report_interval > 0 and (now - getattr(engine, "_last_health_log_ts", 0.0)) < report_interval:
        return
    engine._last_health_log_ts = now
    no_frame_seconds = (now - engine._last_frame_ts) if getattr(engine, "_last_frame_ts", None) else 0.0
    since_last_write = (now - engine._last_write_ts) if getattr(engine, "_last_write_ts", None) else 0.0
    stale_reasons: list[str] = []
    stale_threshold = getattr(engine, "_health_stale_threshold_seconds", 0.0)
    if stale_threshold > 0:
        if should_stream and getattr(engine, "_last_frame_ts", None) and no_frame_seconds >= stale_threshold:
            stale_reasons.append("no_frame")
        if engine._stream_active and getattr(engine, "_last_write_ts", None) and since_last_write >= stale_threshold:
            stale_reasons.append("no_write")
    is_stale = bool(stale_reasons)
    LOGGER.debug(
        "streaming health: state=%s phase=%s should=%s active=%s ffmpeg_alive=%s proc=%d drop=%d fail=%d reconnect=%d no_frame=%.2fs no_write=%.2fs stale=%s stale_reasons=%s threshold=%.2fs err=%s",
        engine._state,
        phase,
        should_stream,
        engine._stream_active,
        engine._ffmpeg.is_alive(),
        engine._processed_frames,
        engine._dropped_frames,
        engine._write_failures,
        engine._reconnect_count,
        no_frame_seconds,
        since_last_write,
        is_stale,
        ",".join(stale_reasons) or "-",
        stale_threshold,
        engine._last_error,
    )


def _resolve_resource_root() -> Path:
    root = os.environ.get("EDGE_RESOURCE_ROOT")
    if not root:
        return Path.cwd()
    candidate = Path(root).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    return candidate


def _get_schedule_path(resource_root: Path) -> Path:
    env_path = os.environ.get("EDGE_SCHEDULE_PATH") or os.environ.get("EDGE_DEMO_SCHEDULE_PATH")
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_absolute():
            candidate = (resource_root / env_path).resolve()
        return candidate
    return resource_root / "schedule.json"
