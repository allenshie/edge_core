"""Shared streaming engine primitives and pacing loop."""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, Sequence

import cv2
from smart_workflow import TaskContext

from edge.runtime.rate_meter import RateMeter
from edge.runtime.shutdown_summary import cleanup_record
from edge.schema import EdgeDetection, FrameMeta
from edge.pipeline.tasks.matching_result.constants import MATCHING_RESULT_LOCAL_TO_GLOBAL_RESOURCE

from .policy import STATE_DEGRADED, STATE_INACTIVE, STATE_STREAMING
from ..types import StreamPacket, StreamingStatus

LOGGER = logging.getLogger(__name__)


def _truncate_text(text: str, max_length: int) -> str:
    value = str(text).strip() or "unknown"
    if max_length <= 0 or len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]
    return value[: max_length - 3].rstrip() + "..."


def _measure_text_width(text: str, font_scale: float, text_thickness: int) -> int:
    text_size, _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_thickness)
    return text_size[0]


def _compose_detection_label(class_name: str, suffix_parts: tuple[str, ...]) -> str:
    parts = [class_name, *suffix_parts]
    return " ".join(part for part in parts if part).strip()


def _fit_detection_label(
    class_name: str,
    suffix_parts: tuple[str, ...],
    *,
    max_width_px: int,
    font_scale: float,
    text_thickness: int,
) -> tuple[str, bool]:
    candidate = _compose_detection_label(class_name, suffix_parts)
    if _measure_text_width(candidate, font_scale, text_thickness) <= max_width_px:
        return candidate, True

    last_candidate = candidate
    for class_limit in range(len(class_name) - 1, 0, -1):
        truncated_class = _truncate_text(class_name, class_limit)
        candidate = _compose_detection_label(truncated_class, suffix_parts)
        last_candidate = candidate
        if _measure_text_width(candidate, font_scale, text_thickness) <= max_width_px:
            return candidate, True
    return last_candidate, _measure_text_width(last_candidate, font_scale, text_thickness) <= max_width_px


def _format_detection_label(
    det: EdgeDetection,
    *,
    show_track_info: bool,
    matching_label: str | None = None,
    max_width_px: int | None = None,
    font_scale: float = 0.4,
    text_thickness: int = 1,
) -> str:
    if matching_label is not None:
        label = _truncate_text(matching_label, 24)
        if max_width_px is None or max_width_px <= 0:
            return label
        return _fit_plain_label(
            label,
            max_width_px=max_width_px,
            font_scale=font_scale,
            text_thickness=text_thickness,
        )

    score = det.score if det.score is not None else det.bbox_confidence_score
    score_value = float(score) if score is not None else 0.0
    class_name = _truncate_text(det.class_name, 18)
    score_text = f"{max(0, min(100, int(round(score_value * 100))))}%"
    track_text = f"#{det.track_id}" if det.track_id is not None else None

    if show_track_info and track_text is not None:
        suffix_variants: tuple[tuple[str, ...], ...] = ((track_text, score_text), (track_text,), (score_text,), ())
    else:
        suffix_variants = ((score_text,), ())

    if max_width_px is None or max_width_px <= 0:
        return _compose_detection_label(class_name, suffix_variants[0])

    for suffix_parts in suffix_variants:
        label, fits = _fit_detection_label(
            class_name,
            suffix_parts,
            max_width_px=max_width_px,
            font_scale=font_scale,
            text_thickness=text_thickness,
        )
        if fits:
            return label

    # 所有候選都超寬時，回傳最短版本，讓畫面至少保留可辨識的類別名稱。
    return _fit_detection_label(
        class_name,
        suffix_variants[-1],
        max_width_px=max_width_px,
        font_scale=font_scale,
        text_thickness=text_thickness,
    )[0]


def _fit_plain_label(
    label: str,
    *,
    max_width_px: int,
    font_scale: float,
    text_thickness: int,
) -> str:
    if _measure_text_width(label, font_scale, text_thickness) <= max_width_px:
        return label

    last_candidate = label
    for label_limit in range(len(label) - 1, 0, -1):
        candidate = _truncate_text(label, label_limit)
        last_candidate = candidate
        if _measure_text_width(candidate, font_scale, text_thickness) <= max_width_px:
            return candidate
    return last_candidate


def _draw_detection_box_and_label(
    vis_frame: Any,
    bbox: Sequence[int],
    label: str,
    *,
    color: tuple[int, int, int] = (0, 255, 0),
    text_color: tuple[int, int, int] = (255, 255, 255),
    font_scale: float = 0.4,
    thickness: int = 1,
    text_thickness: int = 1,
) -> None:
    frame_h, frame_w = vis_frame.shape[:2]
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return

    try:
        x1, y1, x2, y2 = [int(v) for v in bbox]
    except (TypeError, ValueError):
        return

    font_face = cv2.FONT_HERSHEY_SIMPLEX
    label_padding_x = max(4, thickness * 2)
    label_padding_y = max(2, thickness)

    text_size, baseline = cv2.getTextSize(label, font_face, font_scale, text_thickness)
    text_w, text_h = text_size
    label_w = text_w + label_padding_x * 2
    label_h = text_h + baseline + label_padding_y * 2

    # 優先把 label 放在 bbox 上方；若空間不足，則改放到 bbox 下方並在畫面內對齊。
    label_x1 = max(0, min(x1, max(frame_w - label_w, 0)))
    label_x2 = min(frame_w, label_x1 + label_w)
    if y1 >= label_h:
        label_y2 = y1
        label_y1 = y1 - label_h
    else:
        label_y1 = min(max(y2, 0), max(frame_h - label_h, 0))
        label_y2 = min(frame_h, label_y1 + label_h)

    text_org_x = label_x1 + label_padding_x
    text_org_y = label_y2 - label_padding_y - baseline

    cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, thickness)
    cv2.rectangle(vis_frame, (label_x1, label_y1), (label_x2, label_y2), color, -1)
    cv2.putText(
        vis_frame,
        label,
        (text_org_x, text_org_y),
        font_face,
        font_scale,
        text_color,
        text_thickness,
    )


class BaseStreamingEngine(ABC):
    """Common pacing, latest-frame cache and shared health helpers."""

    def __init__(self, context: TaskContext | None = None) -> None:
        self._context = context
        streaming_cfg = getattr(context.config, "streaming", None) if context else None
        visual_cfg = getattr(context.config, "visualization", None) if context else None
        self._stop_event = threading.Event()
        self._stream_active = False
        self._state = STATE_INACTIVE
        self._latest_packet_lock = threading.Lock()
        self._latest_packet: StreamPacket | None = None
        self._output_thread: threading.Thread | None = None
        self._next_output_deadline = 0.0
        self._target_fps = self._resolve_fps(context)
        self._target_period = 1.0 / self._target_fps if self._target_fps > 0 else 1.0 / 30.0
        self._output_size = self._resolve_output_size(streaming_cfg)
        self._last_emitted_identity: tuple[str | None, int | None] | None = None
        self._unique_write_rate = RateMeter()
        show_track_info = getattr(visual_cfg, "show_track_info", False) if visual_cfg is not None else False
        self._show_track_info = bool(show_track_info) if isinstance(show_track_info, bool) else False
        matching_cfg = getattr(context.config, "matching_result", None) if context else None
        self._matching_result_enabled = bool(getattr(matching_cfg, "enabled", False)) if matching_cfg is not None else False
        self._detection_color: tuple[int, int, int] = (
            getattr(visual_cfg, "detection_color_bgr", (0, 255, 0)) if visual_cfg is not None else (0, 255, 0)
        )

    @abstractmethod
    def push(
        self,
        frame: Any,
        detections: Sequence[EdgeDetection],
        phase: str,
        frame_meta: FrameMeta | None = None,
    ) -> StreamingStatus:
        # 子類需負責把最新 frame / detections 寫入 latest snapshot，
        # 再交由 _output_loop() 以固定節拍輸出。
        raise NotImplementedError

    def begin_shutdown(self) -> None:
        self._stop_event.set()

    def close(self) -> list[dict[str, Any]]:
        self.begin_shutdown()
        alive_before = self._output_thread is not None and self._output_thread.is_alive()
        started = time.perf_counter()
        if alive_before and self._output_thread is not None:
            self._output_thread.join(timeout=2.0)
        alive_after = self._output_thread is not None and self._output_thread.is_alive()
        self._output_thread = None
        duration_ms = (time.perf_counter() - started) * 1000.0
        if not alive_before:
            return [
                cleanup_record(
                    item="streaming.pacer",
                    type="thread",
                    state="skipped",
                    ok=True,
                    alive_before=False,
                    alive_after=False,
                    duration_ms=duration_ms,
                    detail="pacer not running",
                )
            ]
        state = "done" if not alive_after else "timeout"
        detail = "pacer stopped" if not alive_after else "pacer still alive after stop"
        return [
            cleanup_record(
                item="streaming.pacer",
                type="thread",
                state=state,
                ok=not alive_after,
                alive_before=alive_before,
                alive_after=alive_after,
                duration_ms=duration_ms,
                detail=detail,
            )
        ]

    def _resolve_fps(self, context: TaskContext | None) -> float:
        if context is None:
            return 30.0
        streaming_cfg = getattr(context.config, "streaming", None)
        if streaming_cfg:
            streaming_fps = getattr(streaming_cfg, "fps", None)
            if streaming_fps and streaming_fps > 0:
                return float(streaming_fps)
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
            elif mode == "camera":
                camera_fps = getattr(ingestion_cfg.camera, "fps", None)
                if camera_fps and camera_fps > 0:
                    return float(camera_fps)
            else:
                rtsp_fps = getattr(ingestion_cfg.rtsp, "fps", None)
                if rtsp_fps and rtsp_fps > 0:
                    return float(rtsp_fps)
        return 30.0

    def _store_latest_packet(self, packet: StreamPacket) -> None:
        # 只保留最新快照，舊包直接覆蓋，避免 queue/backlog 造成延遲累積。
        with self._latest_packet_lock:
            self._latest_packet = packet

    def _get_latest_packet(self) -> StreamPacket | None:
        with self._latest_packet_lock:
            return self._latest_packet

    def _clear_latest_packet(self) -> None:
        with self._latest_packet_lock:
            self._latest_packet = None

    def _start_output_loop(self) -> None:
        if self._output_thread is not None:
            return
        self._next_output_deadline = time.monotonic()
        self._output_thread = threading.Thread(
            target=self._output_loop,
            name="EdgeStreamingPacer",
            daemon=True,
        )
        self._output_thread.start()

    def _output_loop(self) -> None:
        # 單一背景 thread：固定節拍取最新快照，缺新包時會自然重複上一張最新幀。
        LOGGER.info("streaming pacer started: target_fps=%.2f", self._target_fps)
        while not self._stop_event.is_set():
            # 情況 1: streaming 尚未啟用，先保持 idle，等 task 把最新狀態打進來。
            if not self._stream_active:
                self._next_output_deadline = 0.0
                self._stop_event.wait(timeout=0.05)
                continue

            now = time.monotonic()
            # 情況 2: 剛進入 active 狀態，先把下一次輸出 deadline 對齊到現在。
            if self._next_output_deadline <= 0:
                self._next_output_deadline = now
            wait_seconds = self._next_output_deadline - now
            # 情況 3: 還沒到節拍點，短暫等待；這裡不取新包、不重複輸出。
            if wait_seconds > 0:
                self._stop_event.wait(timeout=min(wait_seconds, 0.05))
                continue

            # 情況 4: 到達節拍點，取目前最新快照來輸出。
            packet = self._get_latest_packet()
            if packet is not None:
                try:
                    # 情況 4-1: 有最新 packet，輸出新幀或重複最新幀。
                    self._emit_packet(packet)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("streaming pacing tick failed: %s", exc)
            else:
                # 情況 4-2: 尚未收到任何 packet，這一拍只前進 deadline，保持固定節拍。
                LOGGER.debug("streaming pacing tick skipped: no latest packet")

            # 情況 5: 不論這拍有沒有成功輸出，都要把 deadline 往前推一個週期，
            #         如果前一輪已經落後，_advance_output_deadline() 會自動追趕並跳過過期 tick。
            self._advance_output_deadline(time.monotonic())
        LOGGER.info("streaming pacer stopped")

    def _advance_output_deadline(self, now: float) -> None:
        if self._target_period <= 0:
            self._next_output_deadline = now
            return
        if self._next_output_deadline <= 0:
            self._next_output_deadline = now + self._target_period
        else:
            self._next_output_deadline += self._target_period
        while self._next_output_deadline <= now:
            self._next_output_deadline += self._target_period

    def _emit_packet(self, packet: StreamPacket) -> None:
        if self._stop_event.is_set() or not self._stream_active:
            return

        vis_frame = self._prepare_output_frame(packet)
        if vis_frame is None:
            return
        self._write_output_frame(vis_frame, packet.phase, packet.frame_meta)

    @abstractmethod
    def _prepare_output_frame(self, packet: StreamPacket) -> Any | None:
        """Build the frame to be encoded for the current packet."""
        raise NotImplementedError

    @staticmethod
    def _resolve_output_size(streaming_cfg: Any | None) -> tuple[int, int] | None:
        if streaming_cfg is None:
            return None
        output_width = getattr(streaming_cfg, "output_width", None)
        output_height = getattr(streaming_cfg, "output_height", None)
        if output_width is None and output_height is None:
            return None
        if output_width is None or output_height is None:
            LOGGER.warning(
                "streaming output resize disabled: both EDGE_STREAMING_OUT_WIDTH and EDGE_STREAMING_OUT_HEIGHT must be set (width=%s height=%s)",
                output_width,
                output_height,
            )
            return None
        return int(output_width), int(output_height)

    def _resize_output_frame(self, vis_frame: Any | None) -> Any | None:
        if vis_frame is None or self._output_size is None:
            return vis_frame

        target_width, target_height = self._output_size
        try:
            frame_height, frame_width = vis_frame.shape[:2]
        except Exception:
            return vis_frame

        if frame_width == target_width and frame_height == target_height:
            return vis_frame

        if target_width <= frame_width and target_height <= frame_height:
            interpolation = cv2.INTER_AREA
        else:
            interpolation = cv2.INTER_LINEAR
        return cv2.resize(vis_frame, (target_width, target_height), interpolation=interpolation)

    def _write_output_frame(self, vis_frame: Any, phase: str, frame_meta: FrameMeta | None = None) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._ffmpeg.write_frame(vis_frame)
            # 只有真的寫進 ffmpeg 才算一次有效輸出，這個 rate 才是 stream_output_fps。
            self._processed_frames += 1
            self._last_error = None
            self._last_write_ts = time.time()
            self._state = STATE_STREAMING
            self._write_rate.mark()
            if self._is_unique_output(frame_meta):
                self._unique_write_rate.mark(
                    frame_seq=frame_meta.frame_seq if frame_meta is not None else None,
                    ts=frame_meta.capture_ts if frame_meta is not None else None,
                )
        except Exception as exc:  # noqa: BLE001
            self._write_failures += 1
            self._last_error = str(exc)
            self._state = STATE_DEGRADED

            if self._stop_event.is_set():
                LOGGER.debug(
                    "streaming write failed during shutdown; skip ffmpeg restart: phase=%s error=%s failures=%d",
                    phase,
                    exc,
                    self._write_failures,
                )
                return

            now = time.time()
            if (now - self._last_restart_ts) < self._restart_backoff_seconds:
                LOGGER.warning(
                    "streaming write failed (backoff active): phase=%s error=%s failures=%d",
                    phase,
                    exc,
                    self._write_failures,
                )
                return

            self._last_restart_ts = now
            self._reconnect_count += 1
            LOGGER.warning(
                "streaming write failed; restart ffmpeg: phase=%s error=%s failures=%d reconnect=%d",
                phase,
                exc,
                self._write_failures,
                self._reconnect_count,
            )
            try:
                self._ffmpeg.restart()
            except Exception as restart_exc:  # noqa: BLE001
                self._last_error = str(restart_exc)
                LOGGER.warning("streaming ffmpeg restart failed: %s", restart_exc)

    @property
    def write_rate_meter(self) -> RateMeter:
        return self._write_rate

    @property
    def unique_write_rate_meter(self) -> RateMeter:
        return self._unique_write_rate

    def _is_unique_output(self, frame_meta: FrameMeta | None) -> bool:
        if frame_meta is None:
            return False
        identity = (frame_meta.session_id, frame_meta.frame_seq)
        if self._last_emitted_identity == identity:
            return False
        self._last_emitted_identity = identity
        return True

    def _resolve_matching_overlay_label(self, det: EdgeDetection) -> str | None:
        if not self._matching_result_enabled:
            return None
        local_id = det.track_id
        local_text = f"l:{local_id}" if local_id is not None else "l:-"
        global_id = None
        if self._context is not None and local_id is not None:
            match_table = self._context.get_resource(MATCHING_RESULT_LOCAL_TO_GLOBAL_RESOURCE)
            if isinstance(match_table, Mapping):
                global_id = match_table.get(local_id)
        global_text = f"g:{global_id}" if global_id is not None else "g:-"
        return f"{global_text}, {local_text}"

    def _draw_detections(self, vis_frame: Any, detections: Sequence[EdgeDetection]) -> None:
        frame_h, frame_w = vis_frame.shape[:2]
        # 依 frame 尺寸調整框線粗細與字體，避免不同解析度下可讀性落差太大。
        thickness = max(1, int(min(frame_w, frame_h) / 360))
        text_thickness = max(1, thickness)
        font_scale = max(0.4, min(frame_w, frame_h) / 1200)

        for det in detections:
            bbox = det.bbox
            matching_label = self._resolve_matching_overlay_label(det)
            label = _format_detection_label(
                det,
                show_track_info=self._show_track_info,
                matching_label=matching_label,
                max_width_px=min(frame_w - 8, max(96, int(frame_w * 0.25))),
                font_scale=font_scale,
                text_thickness=text_thickness,
            )
            _draw_detection_box_and_label(
                vis_frame,
                bbox,
                label,
                color=self._detection_color,
                text_color=(255, 255, 255),
                font_scale=font_scale,
                thickness=thickness,
                text_thickness=text_thickness,
            )
