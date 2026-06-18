"""FFmpeg-based recording writer for streaming debug archives."""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from edge.config import RecordingConfig
from edge.runtime.shutdown_summary import cleanup_record
from edge.schema import FrameMeta

LOGGER = logging.getLogger(__name__)
DEFAULT_FILENAME_TEMPLATE = "{camera_id}_{phase}_{start_dt:%Y%m%d_%H%M%S}.mp4"
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_token(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    text = text or "unknown"
    text = text.replace("/", "_").replace("\\", "_")
    text = _SAFE_FILENAME_RE.sub("_", text)
    text = text.strip("._-")
    return text or "unknown"


def _frame_meta_value(frame_meta: FrameMeta | None, name: str, default: Any = None) -> Any:
    if frame_meta is None:
        return default
    return getattr(frame_meta, name, default)


class RecordingCommandFactory:
    @staticmethod
    def build(output_path: Path, *, fps: float, width: int, height: int) -> list[str]:
        fps_value = float(fps) if fps and fps > 0 else 30.0
        fps_text = f"{fps_value:g}"
        return [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            fps_text,
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-vsync",
            "cfr",
            "-f",
            "mp4",
            str(output_path),
        ]


class FfmpegRecordingWriter:
    def __init__(self, config: RecordingConfig, *, fps: float, camera_id: str) -> None:
        self._config = config
        self._fps = fps
        self._camera_id = _sanitize_token(camera_id)
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._width: int | None = None
        self._height: int | None = None
        self._output_dir = Path(config.output_dir).expanduser()
        self._output_path: Path | None = None
        self._started = False
        self._start_dt: datetime | None = None
        self._start_phase: str | None = None
        self._failed = False
        self._last_error: str | None = None
        self._state = "inactive"

    @property
    def output_path(self) -> str | None:
        return str(self._output_path) if self._output_path is not None else None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def is_alive(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None and not self._failed

    def write(self, frame: Any, *, phase: str, frame_meta: FrameMeta | None = None) -> None:
        if frame is None:
            return
        with self._lock:
            if self._failed:
                return
            try:
                self._write_locked(frame, phase=phase, frame_meta=frame_meta)
            except Exception as exc:  # noqa: BLE001
                self._fail_locked(str(exc))

    def close(self) -> list[dict[str, Any]]:
        with self._lock:
            alive_before = self._process is not None and self._process.poll() is None
            started_before = self._started
            failed_before = self._failed
            started_at = time.perf_counter()
            self._stop_process_locked()
            alive_after = self._process is not None and self._process.poll() is None
            duration_ms = (time.perf_counter() - started_at) * 1000.0

            if failed_before:
                state = "failed"
                ok = False
                detail = self._last_error or "recording failed"
            elif started_before:
                state = "done" if not alive_after else "timeout"
                ok = not alive_after
                detail = "recording terminated" if not alive_after else "recording still alive after close"
            else:
                state = "skipped"
                ok = True
                detail = "recording not started"

            return [
                cleanup_record(
                    item="streaming.recording",
                    type="subprocess",
                    state=state,
                    ok=ok,
                    alive_before=alive_before,
                    alive_after=alive_after,
                    duration_ms=duration_ms,
                    detail=detail,
                )
            ]

    def _write_locked(self, frame: Any, *, phase: str, frame_meta: FrameMeta | None) -> None:
        if self._process is not None and self._process.poll() is not None:
            code = self._process.returncode
            self._log_stderr_tail_locked(prefix="recording exited")
            self._stop_process_locked()
            raise RuntimeError(f"recording ffmpeg exited unexpectedly (code={code})")

        try:
            height, width = frame.shape[:2]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"invalid recording frame: {exc}") from exc

        if self._output_path is None:
            self._output_path = self._build_output_path(phase=phase, frame_meta=frame_meta)
        elif self._width is not None and self._height is not None and (self._width != width or self._height != height):
            raise RuntimeError(
                f"recording frame size changed {self._width}x{self._height} -> {width}x{height}"
            )

        self._ensure_process_locked(width=width, height=height)
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("recording ffmpeg process not available")
        if self._process.poll() is not None:
            code = self._process.returncode
            self._log_stderr_tail_locked(prefix="recording exited")
            self._stop_process_locked()
            raise RuntimeError(f"recording ffmpeg exited unexpectedly (code={code})")

        frame_bytes = frame.tobytes()
        expected_size = width * height * 3
        if len(frame_bytes) != expected_size:
            raise RuntimeError(f"invalid recording frame bytes: got={len(frame_bytes)} expected={expected_size}")

        try:
            self._process.stdin.write(frame_bytes)
        except (BrokenPipeError, OSError) as exc:
            self._log_stderr_tail_locked(prefix="recording write failed")
            self._stop_process_locked()
            raise RuntimeError(f"recording ffmpeg write failed: {exc}") from exc

    def _build_output_path(self, *, phase: str, frame_meta: FrameMeta | None) -> Path:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._start_dt = datetime.now().astimezone()
        self._start_phase = _sanitize_token(phase)
        template = (self._config.filename_template or "").strip() or DEFAULT_FILENAME_TEMPLATE
        values = {
            "camera_id": self._camera_id,
            "phase": self._start_phase,
            "start_dt": self._start_dt,
            "start_ts": int(self._start_dt.timestamp()),
            "session_id": _sanitize_token(_frame_meta_value(frame_meta, "session_id", "unknown")),
            "frame_seq": _frame_meta_value(frame_meta, "frame_seq", "unknown"),
            "capture_ts": _frame_meta_value(frame_meta, "capture_ts", "unknown"),
        }
        try:
            filename = template.format(**values)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"invalid recording filename template: {exc}") from exc
        filename = Path(filename).name
        return (self._output_dir / filename).resolve()

    def _ensure_process_locked(self, *, width: int, height: int) -> None:
        self._width = width
        self._height = height
        if self._process is not None and self._process.poll() is None:
            return
        self._start_process_locked(width=width, height=height)

    def _start_process_locked(self, *, width: int, height: int) -> None:
        if self._output_path is None:
            raise RuntimeError("recording output path is empty")
        cmd = RecordingCommandFactory.build(self._output_path, fps=self._fps, width=width, height=height)
        LOGGER.info(
            "starting recording ffmpeg: fps=%.2f size=%dx%d path=%s",
            self._fps,
            width,
            height,
            self._output_path,
        )
        LOGGER.debug("recording ffmpeg command: %s", " ".join(cmd))
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            self._process = None
            raise RuntimeError("ffmpeg not found in PATH") from exc
        self._started = True
        self._state = "recording"

    def _stop_process_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        pid = process.pid
        started_at = time.monotonic()
        LOGGER.info(
            "recording ffmpeg stop begin: pid=%s returncode=%s stdin=%s stderr=%s",
            pid,
            process.returncode,
            bool(process.stdin),
            bool(process.stderr),
        )
        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            LOGGER.exception("recording ffmpeg stdin close failed (pid=%s)", pid)
        try:
            process.wait(timeout=2.5)
            if process.returncode not in (0, None):
                self._log_stderr_tail_from_process(process, prefix="recording exited")
            LOGGER.info(
                "recording ffmpeg process terminated (pid=%s elapsed_ms=%.2f)",
                pid,
                (time.monotonic() - started_at) * 1000.0,
            )
            return
        except subprocess.TimeoutExpired:
            LOGGER.warning("recording ffmpeg did not exit after stdin close, terminating (pid=%s)", pid)
        except Exception:
            LOGGER.exception("recording ffmpeg wait failed (pid=%s)", pid)

        try:
            process.terminate()
            process.wait(timeout=1.5)
            self._log_stderr_tail_from_process(process, prefix="recording terminated")
            LOGGER.info(
                "recording ffmpeg process terminated (pid=%s elapsed_ms=%.2f)",
                pid,
                (time.monotonic() - started_at) * 1000.0,
            )
        except Exception:
            try:
                LOGGER.warning("recording ffmpeg terminate failed, killing process (pid=%s)", pid)
                process.kill()
                process.wait(timeout=1.5)
                self._log_stderr_tail_from_process(process, prefix="recording killed")
            except Exception:
                LOGGER.exception("recording ffmpeg stop failed (pid=%s)", pid)

    def _fail_locked(self, message: str) -> None:
        if self._failed:
            return
        self._failed = True
        self._last_error = message
        self._state = "failed"
        LOGGER.warning("recording disabled: %s", message)
        self._stop_process_locked()

    def _log_stderr_tail_locked(self, prefix: str) -> None:
        if self._process is None:
            return
        self._log_stderr_tail_from_process(self._process, prefix=prefix)

    def _log_stderr_tail_from_process(self, process: subprocess.Popen, prefix: str) -> None:
        if process.stderr is None:
            return
        try:
            data = process.stderr.read()
            if not data:
                return
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            lines = [line.rstrip() for line in data.splitlines() if line.strip()]
            if lines:
                tail = lines[-12:]
                LOGGER.warning("%s stderr: %s", prefix, " | ".join(tail))
        except Exception:
            pass
