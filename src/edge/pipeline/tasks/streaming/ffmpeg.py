"""FFmpeg process lifecycle management for streaming output."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)


@dataclass
class EncoderSpec:
    url: str
    strategy: str
    fps: float


class EncoderCommandFactory:
    @staticmethod
    def build(spec: EncoderSpec, width: int, height: int) -> list[str]:
        fps = max(1, int(round(spec.fps if spec.fps > 0 else 30.0)))
        strategy = (spec.strategy or "cpu").strip().lower()
        codec = "h264_nvenc" if strategy == "gpu" else "libx264"

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            "-",
            "-an",
            "-c:v",
            codec,
            "-tune",
            "zerolatency",
            "-g",
            str(fps),
            "-keyint_min",
            str(fps),
            "-sc_threshold",
            "0",
            "-bf",
            "0",
            "-max_delay",
            "0",
            "-flags",
            "+low_delay",
            "-analyzeduration",
            "0",
            "-max_muxing_queue_size",
            "1024",
        ]

        if codec == "h264_nvenc":
            cmd.extend(["-preset", "p4", "-rc", "cbr", "-b:v", "3000k", "-maxrate", "3000k", "-bufsize", "6000k"])
        else:
            cmd.extend(
                [
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "baseline",
                    "-x264-params",
                    "repeat-headers=1:aud=1",
                    "-b:v",
                    "3000k",
                    "-maxrate",
                    "3000k",
                    "-bufsize",
                    "6000k",
                ]
            )

        # Ubuntu 20.04 內建的 ffmpeg 4.2.7 不支援 -fps_mode，因此改用舊版相容的 -vsync cfr。
        cmd.extend(["-vsync", "cfr"])
        cmd.extend(["-f", "flv", spec.url])
        return cmd


class FfmpegProcessManager:
    def __init__(self, spec: EncoderSpec) -> None:
        self._spec = spec
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._width: int | None = None
        self._height: int | None = None

    def is_alive(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def write_frame(self, frame) -> None:
        if frame is None:
            return
        height, width = frame.shape[:2]
        with self._lock:
            # Restart when frame geometry changes to avoid bitstream corruption.
            if self._width is not None and self._height is not None and (self._width != width or self._height != height):
                LOGGER.warning(
                    "frame size changed %dx%d -> %dx%d, restarting ffmpeg",
                    self._width,
                    self._height,
                    width,
                    height,
                )
                self._stop_process_locked()
            self._ensure_process(width=width, height=height)

            if self._process is None or self._process.stdin is None:
                raise RuntimeError("ffmpeg process not available")
            if self._process.poll() is not None:
                code = self._process.returncode
                self._log_stderr_tail_locked(prefix="ffmpeg exited")
                self._stop_process_locked()
                raise RuntimeError(f"ffmpeg exited unexpectedly (code={code})")

            frame_bytes = frame.tobytes()
            expected_size = width * height * 3
            if len(frame_bytes) != expected_size:
                raise RuntimeError(f"invalid frame bytes: got={len(frame_bytes)} expected={expected_size}")

            try:
                self._process.stdin.write(frame_bytes)
            except (BrokenPipeError, OSError) as exc:
                self._log_stderr_tail_locked(prefix="ffmpeg write failed")
                self._stop_process_locked()
                raise RuntimeError(f"ffmpeg write failed: {exc}") from exc

    def restart(self) -> None:
        with self._lock:
            width = self._width
            height = self._height
            LOGGER.warning("restarting ffmpeg process")
            self._stop_process_locked()
            if width is not None and height is not None:
                self._start_process_locked(width=width, height=height)

    def close(self) -> None:
        with self._lock:
            self._stop_process_locked()

    def _ensure_process(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        if self._process is not None and self._process.poll() is None:
            return
        self._start_process_locked(width=width, height=height)

    def _start_process_locked(self, width: int, height: int) -> None:
        if not self._spec.url:
            raise RuntimeError("streaming url is empty")
        cmd = EncoderCommandFactory.build(self._spec, width=width, height=height)
        LOGGER.info(
            "starting ffmpeg: strategy=%s fps=%.2f size=%dx%d url=%s",
            self._spec.strategy,
            self._spec.fps,
            width,
            height,
            self._spec.url,
        )
        LOGGER.debug("ffmpeg command: %s", " ".join(cmd))
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

    def _stop_process_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            LOGGER.debug("ffmpeg stop skipped: process already absent")
            return
        pid = process.pid
        started_at = time.monotonic()
        LOGGER.info(
            "ffmpeg stop begin: pid=%s returncode=%s stdin=%s stderr=%s",
            pid,
            process.returncode,
            bool(process.stdin),
            bool(process.stderr),
        )
        try:
            if process.stdin:
                LOGGER.debug("ffmpeg stop: closing stdin (pid=%s)", pid)
                process.stdin.close()
                LOGGER.debug("ffmpeg stop: stdin closed (pid=%s)", pid)
        except Exception:
            LOGGER.exception("ffmpeg stop: stdin close failed (pid=%s)", pid)
        try:
            LOGGER.debug("ffmpeg stop: sending terminate (pid=%s)", pid)
            process.terminate()
            LOGGER.debug("ffmpeg stop: waiting for exit (pid=%s)", pid)
            process.wait(timeout=1.5)
            LOGGER.debug(
                "ffmpeg stop: wait completed (pid=%s returncode=%s elapsed_ms=%.2f)",
                pid,
                process.returncode,
                (time.monotonic() - started_at) * 1000.0,
            )
            self._log_stderr_tail_from_process(process, prefix="ffmpeg terminated")
            LOGGER.info("ffmpeg process terminated")
        except Exception:
            try:
                LOGGER.warning("ffmpeg stop: terminate/wait failed, killing process (pid=%s)", pid)
                process.kill()
                LOGGER.debug("ffmpeg stop: waiting after kill (pid=%s)", pid)
                process.wait(timeout=1.5)
                LOGGER.debug(
                    "ffmpeg stop: kill wait completed (pid=%s returncode=%s elapsed_ms=%.2f)",
                    pid,
                    process.returncode,
                    (time.monotonic() - started_at) * 1000.0,
                )
                self._log_stderr_tail_from_process(process, prefix="ffmpeg killed")
                LOGGER.warning("ffmpeg process killed")
            except Exception:
                LOGGER.exception("ffmpeg stop: kill path failed (pid=%s)", pid)

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
