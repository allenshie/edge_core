from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import numpy as np

from edge.pipeline.tasks.streaming.ffmpeg import EncoderCommandFactory, EncoderSpec, FfmpegProcessManager


class _FakeProcess:
    def __init__(self, stdin, *, never_exits: bool = False) -> None:
        self.stdin = stdin
        self.stderr = None
        self.pid = 4242
        self.returncode = None
        self._never_exits = never_exits
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self._never_exits:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


def _manager(**kwargs) -> FfmpegProcessManager:
    return FfmpegProcessManager(
        EncoderSpec(url="rtmp://localhost/test", strategy="cpu", fps=15),
        **kwargs,
    )


def test_blocked_write_does_not_deadlock_close() -> None:
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        os.set_blocking(write_fd, True)

        process = _FakeProcess(os.fdopen(write_fd, "wb", buffering=0))
        manager = _manager(write_timeout_seconds=1.0, stop_timeout_seconds=0.05)
        manager._process = process
        manager._width = 1
        manager._height = 1

        write_done = threading.Event()
        close_done = threading.Event()

        def write_frame() -> None:
            try:
                manager.write_frame(np.zeros((1, 1, 3), dtype=np.uint8))
            except RuntimeError:
                pass
            finally:
                write_done.set()

        def close_manager() -> None:
            manager.close()
            close_done.set()

        writer = threading.Thread(target=write_frame, daemon=True)
        closer = threading.Thread(target=close_manager, daemon=True)
        writer.start()
        time.sleep(0.05)
        closer.start()

        assert close_done.wait(0.4), "close waited behind a blocked stdin write"
        assert write_done.wait(0.4), "blocked writer did not observe process detachment"
    finally:
        os.close(read_fd)


def test_close_is_bounded_when_child_never_exits() -> None:
    read_fd, write_fd = os.pipe()
    process = _FakeProcess(os.fdopen(write_fd, "wb", buffering=0), never_exits=True)
    manager = _manager(stop_timeout_seconds=0.02)
    manager._process = process

    started = time.monotonic()
    manager.close()
    elapsed = time.monotonic() - started

    os.close(read_fd)
    assert elapsed < 0.2
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert manager.is_alive() is False
    manager.close()


def test_stderr_is_drained_while_process_is_running(monkeypatch) -> None:
    script = (
        "import sys; "
        "sys.stderr.write('stderr-line\\n' * 10000 + 'stderr-complete\\n'); sys.stderr.flush(); "
        "sys.stdin.buffer.read(3)"
    )
    monkeypatch.setattr(
        EncoderCommandFactory,
        "build",
        staticmethod(lambda spec, width, height: [sys.executable, "-c", script]),
    )
    manager = _manager(write_timeout_seconds=1.0, stop_timeout_seconds=0.05)

    manager.write_frame(np.zeros((1, 1, 3), dtype=np.uint8))
    process = manager._process
    assert process is not None
    assert process.wait(timeout=1.0) == 0
    with manager._stderr_lock:
        tail = list(manager._stderr_tails[id(process)])
    manager.close()

    assert tail[-1] == "stderr-complete"
    assert manager.is_alive() is False


def test_background_cleanup_cannot_close_replacement_process(monkeypatch) -> None:
    old_read_fd, old_write_fd = os.pipe()
    new_read_fd, new_write_fd = os.pipe()
    old_process = _FakeProcess(os.fdopen(old_write_fd, "wb", buffering=0), never_exits=True)
    replacement = _FakeProcess(os.fdopen(new_write_fd, "wb", buffering=0))
    replacement.pid = 4343
    manager = _manager(stop_timeout_seconds=0.02)
    manager._process = old_process
    manager._width = 1
    manager._height = 1
    monkeypatch.setattr(manager, "_start_process", lambda width, height: replacement)

    assert manager.close_async(reason="phase_disabled:non-working") is True
    installed = manager._ensure_process(width=1, height=1)

    assert installed is replacement
    assert manager._process is replacement
    manager.close()
    assert replacement.kill_calls == 0

    os.close(old_read_fd)
    os.close(new_read_fd)


def test_terminal_close_waits_for_async_cleanup_registration(monkeypatch) -> None:
    read_fd, write_fd = os.pipe()
    process = _FakeProcess(os.fdopen(write_fd, "wb", buffering=0))
    manager = _manager(stop_timeout_seconds=0.02)
    manager._process = process

    cleanup_start_called = threading.Event()
    allow_cleanup_start = threading.Event()
    cleanup_entered = threading.Event()
    allow_cleanup = threading.Event()
    close_done = threading.Event()
    original_thread_start = threading.Thread.start
    original_stop = manager._stop_process

    def gated_thread_start(thread) -> None:
        cleanup_start_called.set()
        assert allow_cleanup_start.wait(1.0)
        original_thread_start(thread)

    def gated_stop(target) -> None:
        cleanup_entered.set()
        assert allow_cleanup.wait(1.0)
        original_stop(target)

    monkeypatch.setattr(threading.Thread, "start", gated_thread_start)
    monkeypatch.setattr(manager, "_stop_process", gated_stop)

    async_caller = threading.Thread(target=lambda: manager.close_async(reason="phase_disabled"), daemon=True)
    original_thread_start(async_caller)
    assert cleanup_start_called.wait(0.5)

    close_caller = threading.Thread(target=lambda: (manager.close(), close_done.set()), daemon=True)
    original_thread_start(close_caller)
    assert close_done.wait(0.05) is False

    allow_cleanup_start.set()
    assert cleanup_entered.wait(0.5)
    assert close_done.wait(0.05) is False
    allow_cleanup.set()

    assert close_done.wait(0.5)
    async_caller.join(timeout=0.5)
    close_caller.join(timeout=0.5)
    assert process.returncode == 0
    os.close(read_fd)
