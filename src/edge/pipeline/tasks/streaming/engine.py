"""Streaming engine with phase-aware stream switch and ffmpeg output."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, Sequence

import cv2
from smart_workflow import TaskContext

from edge.api.mode_server import MODE_RESOURCE
from edge.schema import EdgeDetection

from .ffmpeg import EncoderSpec, FfmpegProcessManager
from .types import StreamPacket, StreamingStatus
from .worker import StreamingWorker

LOGGER = logging.getLogger(__name__)

STATE_INACTIVE = "inactive"
STATE_IDLE = "idle"
STATE_STREAMING = "streaming"
STATE_DEGRADED = "degraded"


class BaseStreamingEngine:
    def __init__(self, context: TaskContext | None = None) -> None:
        self._context = context

    def push(
        self,
        context: TaskContext,
        frame: Any,
        detections: Sequence[EdgeDetection],
        phase: str,
    ) -> StreamingStatus:
        raise NotImplementedError

    def close(self) -> None:
        return None


class DefaultStreamingEngine(BaseStreamingEngine):
    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        cfg = getattr(context.config, "streaming", None) if context else None
        self._enabled = bool(getattr(cfg, "enabled", False))
        self._queue_size = int(getattr(cfg, "queue_size", 30) or 30)
        self._strategy = str(getattr(cfg, "strategy", "cpu") or "cpu")
        self._url = str(getattr(cfg, "url", "") or "")
        self._idle_timeout_seconds = float(getattr(cfg, "idle_timeout_seconds", 3.0) or 3.0)
        self._restart_backoff_seconds = float(getattr(cfg, "restart_backoff_seconds", 1.0) or 1.0)

        self._packet_queue: queue.Queue = queue.Queue(maxsize=max(self._queue_size, 1))
        self._stop_event = threading.Event()
        self._worker = StreamingWorker(self._packet_queue, self._stop_event, self._process_packet)
        self._worker.start()

        self._dropped_frames = 0
        self._processed_frames = 0
        self._enqueued_frames = 0
        self._reconnect_count = 0
        self._write_failures = 0
        self._stream_active = False
        self._state = STATE_INACTIVE
        self._last_error: str | None = None
        self._last_frame_ts: float | None = None
        self._last_write_ts: float | None = None
        self._last_health_log_ts: float = 0.0
        self._last_restart_ts: float = 0.0

        self._streaming_enabled_by_phase = self._load_streaming_schedule()
        self._ffmpeg = FfmpegProcessManager(
            EncoderSpec(
                url=self._url,
                strategy=self._strategy,
                fps=self._resolve_fps(context),
            )
        )
        LOGGER.info(
            "streaming engine initialized: enabled=%s strategy=%s queue_size=%d idle_timeout=%.2fs restart_backoff=%.2fs url=%s",
            self._enabled,
            self._strategy,
            self._queue_size,
            self._idle_timeout_seconds,
            self._restart_backoff_seconds,
            self._url or "<empty>",
        )

    def push(
        self,
        context: TaskContext,
        frame: Any,
        detections: Sequence[EdgeDetection],
        phase: str,
    ) -> StreamingStatus:
        _ = context
        now = time.time()
        should_stream = self._should_stream_for_phase(phase)

        if not should_stream:
            self._deactivate_stream(phase, reason="phase_disabled")
            self._log_health(force=False, phase=phase, should_stream=False)
            return self._build_status(phase=phase, should_stream=False, now=now)

        if not self._activate_stream(phase):
            self._log_health(force=True, phase=phase, should_stream=True)
            return self._build_status(phase=phase, should_stream=True, now=now)

        if frame is None:
            self._last_error = "decoded_frame missing"
            if self._last_frame_ts and (now - self._last_frame_ts) >= self._idle_timeout_seconds:
                self._deactivate_stream(phase, reason="no_frame_timeout")
            self._log_health(force=False, phase=phase, should_stream=True)
            return self._build_status(phase=phase, should_stream=True, now=now)

        self._last_frame_ts = now
        packet = StreamPacket(frame=frame, detections=detections, phase=phase, timestamp=now)

        if self._packet_queue.full():
            try:
                self._packet_queue.get_nowait()
                self._packet_queue.task_done()
                self._dropped_frames += 1
                if self._dropped_frames % 30 == 0:
                    LOGGER.warning("streaming queue dropped frames=%d", self._dropped_frames)
            except queue.Empty:
                pass

        try:
            self._packet_queue.put_nowait(packet)
            self._enqueued_frames += 1
        except queue.Full:
            self._dropped_frames += 1

        self._log_health(force=False, phase=phase, should_stream=True)
        return self._build_status(phase=phase, should_stream=True, now=now)

    def close(self) -> None:
        self._worker.stop()
        self._ffmpeg.close()
        self._stream_active = False
        self._state = STATE_INACTIVE
        LOGGER.info(
            "streaming engine closed: state=%s enqueued=%d processed=%d dropped=%d reconnect=%d failures=%d",
            self._state,
            self._enqueued_frames,
            self._processed_frames,
            self._dropped_frames,
            self._reconnect_count,
            self._write_failures,
        )

    def _process_packet(self, packet: StreamPacket) -> None:
        self._processed_frames += 1
        if not self._stream_active:
            return

        frame = packet.frame
        if frame is None:
            self._last_error = "packet frame is empty"
            return

        vis_frame = frame.copy()
        for det in packet.detections:
            bbox = det.bbox or [0, 0, vis_frame.shape[1] // 2, vis_frame.shape[0] // 2]
            x1, y1, x2, y2 = [int(v) for v in bbox]
            score = det.score if det.score is not None else det.bbox_confidence_score
            label = f"{det.class_name}:{float(score):.2f}"
            cv2.rectangle(vis_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(vis_frame, label, (x1, max(y1 - 5, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        try:
            self._ffmpeg.write_frame(vis_frame)
            self._last_error = None
            self._last_write_ts = time.time()
            self._state = STATE_STREAMING
        except Exception as exc:  # noqa: BLE001
            self._write_failures += 1
            self._last_error = str(exc)
            self._state = STATE_DEGRADED

            now = time.time()
            if (now - self._last_restart_ts) < self._restart_backoff_seconds:
                LOGGER.warning(
                    "streaming write failed (backoff active): phase=%s error=%s failures=%d",
                    packet.phase,
                    exc,
                    self._write_failures,
                )
                return

            self._last_restart_ts = now
            self._reconnect_count += 1
            LOGGER.warning(
                "streaming write failed; restart ffmpeg: phase=%s error=%s failures=%d reconnect=%d",
                packet.phase,
                exc,
                self._write_failures,
                self._reconnect_count,
            )
            try:
                self._ffmpeg.restart()
            except Exception as restart_exc:  # noqa: BLE001
                self._last_error = str(restart_exc)
                LOGGER.warning("streaming ffmpeg restart failed: %s", restart_exc)

    def _should_stream_for_phase(self, phase: str) -> bool:
        if not self._enabled:
            return False
        phase_enabled = self._streaming_enabled_by_phase.get(phase)
        if phase_enabled is None:
            return self._enabled
        return self._enabled and phase_enabled

    def _activate_stream(self, phase: str) -> bool:
        if self._stream_active:
            return True
        if not self._url:
            self._last_error = "EDGE_STREAMING_URL is empty"
            self._state = STATE_DEGRADED
            LOGGER.warning("streaming requested but url is empty (phase=%s)", phase)
            return False
        self._stream_active = True
        self._state = STATE_STREAMING
        LOGGER.info("streaming activated (phase=%s)", phase)
        return True

    def _deactivate_stream(self, phase: str, reason: str) -> None:
        if not self._stream_active:
            if reason == "no_frame_timeout":
                self._state = STATE_IDLE
            return
        self._stream_active = False
        self._state = STATE_IDLE if reason == "no_frame_timeout" else STATE_INACTIVE
        self._clear_queue()
        self._ffmpeg.close()
        LOGGER.info("streaming deactivated (phase=%s reason=%s)", phase, reason)

    def _clear_queue(self) -> None:
        while True:
            try:
                self._packet_queue.get_nowait()
                self._packet_queue.task_done()
            except queue.Empty:
                break

    def _build_status(self, phase: str, should_stream: bool, now: float) -> StreamingStatus:
        no_frame_seconds = (now - self._last_frame_ts) if self._last_frame_ts else 0.0
        since_last_write = (now - self._last_write_ts) if self._last_write_ts else 0.0
        return StreamingStatus(
            queue_size=self._packet_queue.qsize(),
            dropped_frames=self._dropped_frames,
            processed_frames=self._processed_frames,
            stream_active=self._stream_active,
            should_stream=should_stream,
            phase=phase,
            enabled=self._enabled,
            last_error=self._last_error,
            state=self._state,
            reconnect_count=self._reconnect_count,
            write_failures=self._write_failures,
            no_frame_seconds=no_frame_seconds,
            since_last_write_seconds=since_last_write,
        )

    def _log_health(self, force: bool, phase: str, should_stream: bool) -> None:
        now = time.time()
        if not force and (now - self._last_health_log_ts) < 5.0:
            return
        self._last_health_log_ts = now
        no_frame_seconds = (now - self._last_frame_ts) if self._last_frame_ts else 0.0
        since_last_write = (now - self._last_write_ts) if self._last_write_ts else 0.0
        LOGGER.info(
            "streaming health: state=%s phase=%s should=%s active=%s ffmpeg_alive=%s q=%d enq=%d proc=%d drop=%d fail=%d reconnect=%d no_frame=%.2fs no_write=%.2fs err=%s",
            self._state,
            phase,
            should_stream,
            self._stream_active,
            self._ffmpeg.is_alive(),
            self._packet_queue.qsize(),
            self._enqueued_frames,
            self._processed_frames,
            self._dropped_frames,
            self._write_failures,
            self._reconnect_count,
            no_frame_seconds,
            since_last_write,
            self._last_error,
        )

    def _resolve_resource_root(self) -> Path:
        root = os.environ.get("EDGE_RESOURCE_ROOT")
        if not root:
            return Path.cwd()
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        return candidate

    def _get_schedule_path(self, resource_root: Path) -> Path:
        env_path = os.environ.get("EDGE_SCHEDULE_PATH") or os.environ.get("EDGE_DEMO_SCHEDULE_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if not candidate.is_absolute():
                candidate = (resource_root / env_path).resolve()
            return candidate
        return resource_root / "schedule.json"

    def _load_streaming_schedule(self) -> Dict[str, bool]:
        resource_root = self._resolve_resource_root()
        schedule_path = self._get_schedule_path(resource_root)
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

    def resolve_phase(self, context: TaskContext) -> str:
        phase = context.get_resource(MODE_RESOURCE)
        if not phase:
            phase = (
                os.environ.get("EDGE_MODE_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT_PHASE")
                or "working_stage_1"
            )
        return str(phase)

    def _resolve_fps(self, context: TaskContext | None) -> float:
        if context is None:
            return 30.0
        ingestion_cfg = getattr(context.config, "ingestion", None)
        if ingestion_cfg:
            mode = (getattr(ingestion_cfg, "mode", "rtsp") or "rtsp").strip().lower()
            if mode == "file":
                file_fps = getattr(ingestion_cfg.file, "fps", None)
                if file_fps and file_fps > 0:
                    return float(file_fps)
                rtsp_fps = getattr(ingestion_cfg.rtsp, "fps", None)
                if rtsp_fps and rtsp_fps > 0:
                    return float(rtsp_fps)
            else:
                rtsp_fps = getattr(ingestion_cfg.rtsp, "fps", None)
                if rtsp_fps and rtsp_fps > 0:
                    return float(rtsp_fps)
        return 30.0
