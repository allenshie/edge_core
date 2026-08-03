"""FFmpeg process lifecycle management for streaming output."""
from __future__ import annotations

import logging
import os
import select
import subprocess
import threading
import time
from collections import deque
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
    def __init__(
        self,
        spec: EncoderSpec,
        *,
        write_timeout_seconds: float = 1.0,
        stop_timeout_seconds: float = 1.0,
    ) -> None:
        self._spec = spec
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._width: int | None = None
        self._height: int | None = None
        self._generation = 0
        self._closed = False
        self._write_timeout_seconds = max(0.05, float(write_timeout_seconds))
        self._stop_timeout_seconds = max(0.01, float(stop_timeout_seconds))
        self._stderr_lock = threading.Lock()
        self._stderr_tails: dict[int, deque[str]] = {}
        self._stderr_threads: dict[int, threading.Thread] = {}
        self._cleanup_lock = threading.Lock()
        self._cleanup_threads: set[threading.Thread] = set()

    def is_alive(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def lifecycle_generation(self) -> int:
        with self._lock:
            return self._generation

    def write_frame(self, frame) -> None:
        if frame is None:
            return
        height, width = frame.shape[:2]
        frame_bytes = frame.tobytes()
        expected_size = width * height * 3
        if len(frame_bytes) != expected_size:
            raise RuntimeError(f"invalid frame bytes: got={len(frame_bytes)} expected={expected_size}")

        process = self._ensure_process(width=width, height=height)
        if process.stdin is None:
            raise RuntimeError("ffmpeg process not available")
        if process.poll() is not None:
            code = process.returncode
            self._log_stderr_tail_from_process(process, prefix="ffmpeg exited")
            detached = self._detach_process(process)
            if detached is not None:
                self._stop_process(detached)
            raise RuntimeError(f"ffmpeg exited unexpectedly (code={code})")

        try:
            self._write_bytes(process, frame_bytes)
        except (BrokenPipeError, OSError, TimeoutError, RuntimeError) as exc:
            self._log_stderr_tail_from_process(process, prefix="ffmpeg write failed")
            detached = self._detach_process(process)
            if detached is not None:
                self._stop_process(detached)
            raise RuntimeError(f"ffmpeg write failed: {exc}") from exc

    def restart(self, *, expected_generation: int | None = None) -> bool:
        with self._lock:
            if self._closed or (
                expected_generation is not None and self._generation != expected_generation
            ):
                LOGGER.info(
                    "ffmpeg restart skipped: closed=%s expected_generation=%s current_generation=%s",
                    self._closed,
                    expected_generation,
                    self._generation,
                )
                return False
            process = self._process
            self._process = None
            self._generation += 1
            generation = self._generation
            width = self._width
            height = self._height
        LOGGER.warning("restarting ffmpeg process")
        if process is not None:
            self._stop_process(process)
        if width is None or height is None:
            return False
        replacement = self._start_process(width=width, height=height)
        with self._lock:
            install = not self._closed and self._generation == generation and self._process is None
            if install:
                self._process = replacement
        if not install:
            LOGGER.info("ffmpeg restart cancelled by newer lifecycle request (pid=%s)", replacement.pid)
            self._stop_process(replacement)
        return install

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self._process
            self._process = None
            self._generation += 1
            with self._cleanup_lock:
                cleanup_threads = list(self._cleanup_threads)
        if process is not None:
            self._stop_process(process)
        self._join_cleanup_threads(cleanup_threads)

    def close_async(self, *, reason: str = "unspecified") -> bool:
        with self._lock:
            if self._closed:
                LOGGER.debug("ffmpeg async stop skipped: manager closed (reason=%s)", reason)
                return False
            process = self._process
            self._process = None
            self._generation += 1
            if process is None:
                LOGGER.debug("ffmpeg async stop skipped: process already absent (reason=%s)", reason)
                return False
            thread = threading.Thread(
                target=self._cleanup_process,
                args=(process, reason),
                name=f"FfmpegCleanup-{process.pid}",
                daemon=True,
            )
            with self._cleanup_lock:
                self._cleanup_threads.add(thread)
            LOGGER.info("ffmpeg async stop scheduled: pid=%s reason=%s", process.pid, reason)
            # Starting while holding the lifecycle lock closes the detach/register
            # gap: terminal close either happens before this block or observes the thread.
            thread.start()
        return True

    def _detach_process(self, expected: subprocess.Popen) -> subprocess.Popen | None:
        with self._lock:
            if self._process is not expected:
                return None
            self._process = None
            self._generation += 1
            return expected

    def _ensure_process(self, width: int, height: int) -> subprocess.Popen:
        with self._lock:
            if self._closed:
                raise RuntimeError("ffmpeg process manager is closed")
            process = self._process
            size_changed = self._width is not None and self._height is not None and (
                self._width != width or self._height != height
            )
            if process is not None and process.poll() is None and not size_changed:
                self._width = width
                self._height = height
                return process
            self._process = None
            self._width = width
            self._height = height
            self._generation += 1
            generation = self._generation

        if size_changed:
            LOGGER.warning("frame size changed to %dx%d, restarting ffmpeg", width, height)
        if process is not None:
            self._stop_process(process)

        replacement = self._start_process(width=width, height=height)
        with self._lock:
            install = not self._closed and self._generation == generation and self._process is None
            if install:
                self._process = replacement
        if install:
            return replacement

        LOGGER.info("ffmpeg start cancelled by newer lifecycle request (pid=%s)", replacement.pid)
        self._stop_process(replacement)
        raise RuntimeError("ffmpeg start cancelled")

    def _start_process(self, width: int, height: int) -> subprocess.Popen:
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
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg not found in PATH") from exc
        if process.stdin is None:
            process.kill()
            raise RuntimeError("ffmpeg stdin is unavailable")
        try:
            os.set_blocking(process.stdin.fileno(), False)
        except (AttributeError, OSError) as exc:
            process.kill()
            process.wait(timeout=self._stop_timeout_seconds)
            raise RuntimeError("failed to configure non-blocking ffmpeg stdin") from exc
        self._start_stderr_drain(process)
        return process

    def _write_bytes(self, process: subprocess.Popen, data: bytes) -> None:
        stdin = process.stdin
        if stdin is None:
            raise RuntimeError("ffmpeg stdin is unavailable")
        fd = stdin.fileno()
        try:
            os.set_blocking(fd, False)
        except OSError:
            pass
        pending = memoryview(data)
        deadline = time.monotonic() + self._write_timeout_seconds
        while pending:
            with self._lock:
                if self._process is not process:
                    raise RuntimeError("ffmpeg write cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"ffmpeg stdin write timed out after {self._write_timeout_seconds:.2f}s")
            _, writable, _ = select.select([], [fd], [], min(0.05, remaining))
            if not writable:
                continue
            try:
                written = os.write(fd, pending)
            except BlockingIOError:
                continue
            if written <= 0:
                raise BrokenPipeError("ffmpeg stdin write returned zero bytes")
            pending = pending[written:]

    def _stop_process(self, process: subprocess.Popen) -> None:
        pid = process.pid
        started_at = time.monotonic()
        LOGGER.info(
            "ffmpeg stop begin: pid=%s returncode=%s stdin=%s stderr=%s",
            pid,
            process.returncode,
            bool(process.stdin),
            bool(process.stderr),
        )
        self._close_stdin(process)
        try:
            LOGGER.debug("ffmpeg stop: waiting for graceful exit after stdin close (pid=%s)", pid)
            process.wait(timeout=self._stop_timeout_seconds)
            LOGGER.debug(
                "ffmpeg stop: wait completed (pid=%s returncode=%s elapsed_ms=%.2f)",
                pid,
                process.returncode,
                (time.monotonic() - started_at) * 1000.0,
            )
            if process.returncode not in (0, None):
                self._log_stderr_tail_from_process(process, prefix="ffmpeg exited")
            LOGGER.info("ffmpeg process terminated")
            self._finish_stderr_drain(process)
            return
        except subprocess.TimeoutExpired:
            LOGGER.warning("ffmpeg stop: graceful exit timed out, terminating (pid=%s)", pid)
        except Exception:
            LOGGER.exception("ffmpeg stop: wait failed (pid=%s)", pid)

        try:
            LOGGER.debug("ffmpeg stop: sending terminate (pid=%s)", pid)
            process.terminate()
            LOGGER.debug("ffmpeg stop: waiting for exit (pid=%s)", pid)
            process.wait(timeout=self._stop_timeout_seconds)
            LOGGER.debug(
                "ffmpeg stop: wait completed (pid=%s returncode=%s elapsed_ms=%.2f)",
                pid,
                process.returncode,
                (time.monotonic() - started_at) * 1000.0,
            )
            self._log_stderr_tail_from_process(process, prefix="ffmpeg terminated")
            LOGGER.info("ffmpeg process terminated")
        except Exception:  # noqa: BLE001
            try:
                LOGGER.warning("ffmpeg stop: terminate/wait failed, killing process (pid=%s)", pid)
                process.kill()
                LOGGER.debug("ffmpeg stop: waiting after kill (pid=%s)", pid)
                process.wait(timeout=self._stop_timeout_seconds)
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
        finally:
            self._finish_stderr_drain(process)

    def _close_stdin(self, process: subprocess.Popen) -> None:
        stdin = process.stdin
        if stdin is None:
            return
        LOGGER.debug("ffmpeg stop: closing stdin (pid=%s)", process.pid)
        try:
            # Popen uses an unbuffered FileIO (bufsize=0), so close only releases
            # the descriptor and cannot flush an arbitrary userspace buffer.
            stdin.close()
            process.stdin = None
            LOGGER.debug("ffmpeg stop: stdin closed (pid=%s)", process.pid)
        except Exception:  # noqa: BLE001
            LOGGER.exception("ffmpeg stop: stdin close failed (pid=%s)", process.pid)

    def _start_stderr_drain(self, process: subprocess.Popen) -> None:
        if process.stderr is None:
            return
        key = id(process)
        thread = threading.Thread(
            target=self._drain_stderr,
            args=(process,),
            name=f"FfmpegStderr-{process.pid}",
            daemon=True,
        )
        with self._stderr_lock:
            self._stderr_tails[key] = deque(maxlen=12)
            self._stderr_threads[key] = thread
        thread.start()

    def _drain_stderr(self, process: subprocess.Popen) -> None:
        stderr = process.stderr
        if stderr is None:
            return
        key = id(process)
        try:
            for raw_line in iter(stderr.readline, b""):
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                else:
                    line = str(raw_line).rstrip()
                if line:
                    with self._stderr_lock:
                        tail = self._stderr_tails.get(key)
                        if tail is not None:
                            tail.append(line)
        except (OSError, ValueError):
            return

    def _finish_stderr_drain(self, process: subprocess.Popen) -> None:
        key = id(process)
        stderr = process.stderr
        if stderr is not None and process.poll() is not None:
            try:
                stderr.close()
            except Exception:  # noqa: BLE001
                pass
        with self._stderr_lock:
            thread = self._stderr_threads.get(key)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.1)
            if thread.is_alive():
                LOGGER.warning("ffmpeg stderr drain still alive after bounded stop (pid=%s)", process.pid)
        with self._stderr_lock:
            self._stderr_threads.pop(key, None)
            self._stderr_tails.pop(key, None)

    def _cleanup_process(self, process: subprocess.Popen, reason: str) -> None:
        try:
            LOGGER.info("ffmpeg background stop begin: pid=%s reason=%s", process.pid, reason)
            self._stop_process(process)
            LOGGER.info("ffmpeg background stop complete: pid=%s reason=%s", process.pid, reason)
        finally:
            current = threading.current_thread()
            with self._cleanup_lock:
                self._cleanup_threads.discard(current)

    def _join_cleanup_threads(self, threads: list[threading.Thread] | None = None) -> None:
        deadline = time.monotonic() + (3 * self._stop_timeout_seconds) + 0.2
        if threads is None:
            with self._cleanup_lock:
                threads = list(self._cleanup_threads)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        with self._cleanup_lock:
            alive = [thread.name for thread in self._cleanup_threads if thread.is_alive()]
        if alive:
            LOGGER.warning("ffmpeg background cleanup still running after bounded close: %s", alive)

    def _log_stderr_tail_locked(self, prefix: str) -> None:
        with self._lock:
            process = self._process
        if process is None:
            return
        self._log_stderr_tail_from_process(process, prefix=prefix)

    def _log_stderr_tail_from_process(self, process: subprocess.Popen, prefix: str) -> None:
        with self._stderr_lock:
            tail = list(self._stderr_tails.get(id(process), ()))
        if tail:
            LOGGER.warning("%s stderr: %s", prefix, " | ".join(tail))
