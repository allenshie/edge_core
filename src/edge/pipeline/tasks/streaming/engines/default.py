"""Default streaming engine implementation."""
from __future__ import annotations

import logging
import time
from typing import Any, Sequence

from smart_workflow import TaskContext

from edge.runtime.rate_meter import RateMeter
from edge.runtime.shutdown_summary import cleanup_record
from edge.schema import EdgeDetection, FrameMeta

from .base import BaseStreamingEngine
from .policy import (
    activate_stream,
    build_status,
    deactivate_stream,
    STATE_INACTIVE,
    load_streaming_schedule,
    log_health,
    should_stream_for_phase,
)
from ..ffmpeg import EncoderSpec, FfmpegProcessManager
from ..types import StreamPacket, StreamingStatus

LOGGER = logging.getLogger(__name__)


class DefaultStreamingEngine(BaseStreamingEngine):
    def __init__(self, context: TaskContext | None = None, *, start_output_loop: bool = True) -> None:
        super().__init__(context)
        cfg = getattr(context.config, "streaming", None) if context else None
        self._enabled = bool(getattr(cfg, "enabled", False))
        self._strategy = str(getattr(cfg, "strategy", "cpu") or "cpu")
        self._url = str(getattr(cfg, "url", "") or "")
        self._idle_timeout_seconds = float(getattr(cfg, "idle_timeout_seconds", 3.0) or 3.0)
        self._restart_backoff_seconds = float(getattr(cfg, "restart_backoff_seconds", 1.0) or 1.0)
        self._health_report_interval_seconds = float(
            getattr(context.config, "health_report_interval_seconds", 5.0) if context else 5.0
        )
        self._health_stale_threshold_seconds = float(
            getattr(context.config, "health_stale_threshold_seconds", 5.0) if context else 5.0
        )

        self._dropped_frames = 0
        self._processed_frames = 0
        self._reconnect_count = 0
        self._write_failures = 0
        self._stream_active = False
        self._state = STATE_INACTIVE
        self._last_error: str | None = None
        self._last_frame_ts: float | None = None
        self._last_write_ts: float | None = None
        self._last_health_log_ts: float = 0.0
        self._last_restart_ts: float = 0.0
        self._write_rate = RateMeter()

        self._streaming_enabled_by_phase = load_streaming_schedule(context)
        self._ffmpeg = FfmpegProcessManager(
            EncoderSpec(
                url=self._url,
                strategy=self._strategy,
                fps=self._target_fps,
            )
        )
        LOGGER.info(
            "streaming engine initialized: enabled=%s strategy=%s target_fps=%.2f idle_timeout=%.2fs restart_backoff=%.2fs url=%s",
            self._enabled,
            self._strategy,
            self._target_fps,
            self._idle_timeout_seconds,
            self._restart_backoff_seconds,
            self._url or "<empty>",
        )
        if start_output_loop:
            self._start_output_loop()

    def push(
        self,
        frame: Any,
        detections: Sequence[EdgeDetection],
        phase: str,
        frame_meta: FrameMeta | None = None,
    ) -> StreamingStatus:
        now = time.time()
        should_stream = should_stream_for_phase(self._enabled, self._streaming_enabled_by_phase, phase)

        if not should_stream:
            deactivate_stream(self, phase, reason="phase_disabled")
            log_health(self, force=False, phase=phase, should_stream=False)
            return build_status(self, phase=phase, should_stream=False, now=now)

        if not activate_stream(self, phase):
            log_health(self, force=True, phase=phase, should_stream=True)
            return build_status(self, phase=phase, should_stream=True, now=now)

        if frame is None:
            self._last_error = "decoded_frame missing"
            if self._last_frame_ts and (now - self._last_frame_ts) >= self._idle_timeout_seconds:
                deactivate_stream(self, phase, reason="no_frame_timeout")
            log_health(self, force=False, phase=phase, should_stream=True)
            return build_status(self, phase=phase, should_stream=True, now=now)

        # task 只負責更新最新畫面與最新 detections；
        # 真正的固定節拍與丟幀 / 重複幀決策交給 _output_loop。
        self._last_frame_ts = now
        packet = StreamPacket(
            frame=frame,
            detections=detections,
            phase=phase,
            timestamp=now,
            frame_meta=frame_meta,
        )
        self._store_latest_packet(packet)

        log_health(self, force=False, phase=phase, should_stream=True)
        return build_status(self, phase=phase, should_stream=True, now=now)

    def close(self) -> list[dict[str, Any]]:
        records = super().close()
        ffmpeg_alive_before = self._ffmpeg.is_alive()
        started = time.perf_counter()
        self._ffmpeg.close()
        ffmpeg_alive_after = self._ffmpeg.is_alive()
        duration_ms = (time.perf_counter() - started) * 1000.0
        if not ffmpeg_alive_before:
            records.append(
                cleanup_record(
                    item="streaming.ffmpeg",
                    type="subprocess",
                    state="skipped",
                    ok=True,
                    alive_before=False,
                    alive_after=False,
                    duration_ms=duration_ms,
                    detail="ffmpeg not running",
                )
            )
        else:
            records.append(
                cleanup_record(
                    item="streaming.ffmpeg",
                    type="subprocess",
                    state="done" if not ffmpeg_alive_after else "timeout",
                    ok=not ffmpeg_alive_after,
                    alive_before=ffmpeg_alive_before,
                    alive_after=ffmpeg_alive_after,
                    duration_ms=duration_ms,
                    detail="ffmpeg terminated" if not ffmpeg_alive_after else "ffmpeg still alive after close",
                )
            )
        self._stream_active = False
        self._state = STATE_INACTIVE
        LOGGER.info(
            "streaming engine closed: state=%s processed=%d dropped=%d reconnect=%d failures=%d",
            self._state,
            self._processed_frames,
            self._dropped_frames,
            self._reconnect_count,
            self._write_failures,
        )
        return records

    def _prepare_output_frame(self, packet: StreamPacket) -> Any | None:
        frame = packet.frame
        if frame is None:
            self._last_error = "packet frame is empty"
            return None

        vis_frame = frame.copy()
        self._draw_detections(vis_frame, packet.detections)
        return vis_frame
