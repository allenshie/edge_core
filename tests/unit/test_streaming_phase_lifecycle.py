from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import numpy as np
from smart_workflow import ProbeConfig, TaskContext
from smart_workflow.health_server import _evaluate_liveness, _evaluate_readiness

from edge.pipeline.tasks.streaming.engines.default import DefaultStreamingEngine
from edge.pipeline.tasks.streaming.engines.policy import STATE_INACTIVE, deactivate_stream
from edge.pipeline.tasks.streaming.types import StreamingStatus
from edge.runtime.health_runtime import EdgeHealthState
from edge.runtime.rate_meter import RateMeter
from edge.runtime.task_health import TaskHealthReporter
from edge.schema import StageStats


class _Monitor:
    def report_event(self, *args, **kwargs) -> None:
        pass


class _FakeFfmpeg:
    def __init__(self) -> None:
        self.async_close_reasons: list[str] = []
        self.restart_calls = 0
        self.generation = 0

    def is_alive(self) -> bool:
        return False

    def close_async(self, *, reason: str) -> bool:
        self.generation += 1
        self.async_close_reasons.append(reason)
        return True

    def close(self) -> None:
        time.sleep(0.3)

    def lifecycle_generation(self) -> int:
        return self.generation

    def restart(self, *, expected_generation: int | None = None) -> bool:
        if expected_generation is not None and expected_generation != self.generation:
            return False
        self.restart_calls += 1
        return True

    def write_frame(self, frame) -> None:
        raise RuntimeError("write cancelled")


class _SuccessfulFfmpeg(_FakeFfmpeg):
    def write_frame(self, frame) -> None:
        pass


def _context(*, streaming_enabled: bool = True) -> TaskContext:
    config = SimpleNamespace(
        camera=SimpleNamespace(camera_id="cam-1"),
        streaming=SimpleNamespace(
            enabled=streaming_enabled,
            fps=15,
            url="rtmp://localhost/test",
            strategy="cpu",
            idle_timeout_seconds=3.0,
            restart_backoff_seconds=1.0,
            recording=SimpleNamespace(enabled=False),
            output_width=None,
            output_height=None,
        ),
        ingestion=SimpleNamespace(
            mode="file",
            file=SimpleNamespace(fps=15),
            camera=SimpleNamespace(fps=15),
            rtsp=SimpleNamespace(fps=15),
        ),
        overlay=SimpleNamespace(show_track_info=False, show_score_info=False),
        matching_result=SimpleNamespace(enabled=False, resource_name="matching_result_snapshot"),
        health_report_interval_seconds=5.0,
        health_stale_threshold_seconds=5.0,
    )
    return TaskContext(logger=logging.getLogger("test"), config=config, monitor=_Monitor())


def _healthy_state() -> EdgeHealthState:
    state = EdgeHealthState(logging.getLogger("test"))
    state.mark_startup_ok()
    state.mark_loop_tick()
    state.mark_progress()
    return state


def _probe_config() -> ProbeConfig:
    return ProbeConfig(
        liveness_timeout_seconds=1.0,
        readiness_timeout_seconds=1.0,
        startup_grace_seconds=1.0,
    )


def test_working_non_working_working_transition_does_not_wait_for_close() -> None:
    context = _context()
    health_state = EdgeHealthState(logging.getLogger("test"))
    context.set_resource("health_state", health_state)
    engine = DefaultStreamingEngine(context, start_output_loop=False)
    fake_ffmpeg = _FakeFfmpeg()
    engine._ffmpeg = fake_ffmpeg
    engine._streaming_enabled_by_phase = {"working": True, "non-working": False}
    frame = np.zeros((2, 2, 3), dtype=np.uint8)

    working = engine.push(frame, [], "working")
    working_health = health_state.snapshot()
    started = time.monotonic()
    non_working = engine.push(frame, [], "non-working")
    non_working_health = health_state.snapshot()
    elapsed = time.monotonic() - started
    resumed = engine.push(frame, [], "working")
    resumed_health = health_state.snapshot()

    assert working.should_stream is True
    assert working.stream_active is True
    assert working_health.in_backoff is False
    assert elapsed < 0.1
    assert non_working.should_stream is False
    assert non_working.stream_active is False
    assert non_working_health.in_backoff is False
    assert fake_ffmpeg.async_close_reasons == ["phase_disabled:non-working"]
    assert resumed.should_stream is True
    assert resumed.stream_active is True
    assert resumed_health.in_backoff is False


def test_readiness_does_not_require_first_successful_stream_write() -> None:
    context = _context()
    health_state = _healthy_state()
    context.set_resource("health_state", health_state)
    engine = DefaultStreamingEngine(context, start_output_loop=False)
    engine._ffmpeg = _SuccessfulFfmpeg()
    engine._streaming_enabled_by_phase = {"working": True}
    frame = np.zeros((2, 2, 3), dtype=np.uint8)

    engine.push(frame, [], "working")
    ready, _ = _evaluate_readiness(health_state.snapshot(), time.time(), _probe_config())
    assert ready is True

    engine._write_output_frame(frame, "stale-working")
    ready, _ = _evaluate_readiness(health_state.snapshot(), time.time(), _probe_config())
    assert ready is True

    engine._write_output_frame(frame, "working")
    ready, _ = _evaluate_readiness(health_state.snapshot(), time.time(), _probe_config())
    assert ready is True


def test_late_successful_write_after_deactivation_does_not_change_readiness() -> None:
    class DelayedSuccessfulFfmpeg(_SuccessfulFfmpeg):
        def __init__(self) -> None:
            super().__init__()
            self.write_entered = threading.Event()
            self.allow_write = threading.Event()

        def write_frame(self, frame) -> None:
            self.write_entered.set()
            assert self.allow_write.wait(1.0)

    context = _context()
    health_state = _healthy_state()
    context.set_resource("health_state", health_state)
    engine = DefaultStreamingEngine(context, start_output_loop=False)
    ffmpeg = DelayedSuccessfulFfmpeg()
    engine._ffmpeg = ffmpeg
    engine._streaming_enabled_by_phase = {"working": True, "non-working": False}
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    engine.push(frame, [], "working")

    writer = threading.Thread(target=lambda: engine._write_output_frame(frame, "working"), daemon=True)
    writer.start()
    assert ffmpeg.write_entered.wait(0.5)

    engine.push(frame, [], "non-working")
    ready, _ = _evaluate_readiness(health_state.snapshot(), time.time(), _probe_config())
    assert ready is True
    ffmpeg.allow_write.set()
    writer.join(timeout=0.5)

    assert writer.is_alive() is False
    ready, _ = _evaluate_readiness(health_state.snapshot(), time.time(), _probe_config())
    assert ready is True


def test_cancelled_write_during_deactivation_does_not_restart_ffmpeg() -> None:
    engine = DefaultStreamingEngine(_context(), start_output_loop=False)
    fake_ffmpeg = _FakeFfmpeg()
    engine._ffmpeg = fake_ffmpeg
    engine._stream_active = False
    engine._state = STATE_INACTIVE

    engine._write_output_frame(np.zeros((2, 2, 3), dtype=np.uint8), "non-working")

    assert fake_ffmpeg.restart_calls == 0
    assert engine._state == STATE_INACTIVE
    assert engine._last_error is None


def test_deactivation_during_failure_rejects_late_restart() -> None:
    class RacingFfmpeg(_FakeFfmpeg):
        def __init__(self) -> None:
            super().__init__()
            self.restart_entered = threading.Event()
            self.allow_restart = threading.Event()

        def restart(self, *, expected_generation: int | None = None) -> bool:
            self.restart_entered.set()
            assert self.allow_restart.wait(1.0)
            return super().restart(expected_generation=expected_generation)

    engine = DefaultStreamingEngine(_context(), start_output_loop=False)
    ffmpeg = RacingFfmpeg()
    engine._ffmpeg = ffmpeg
    engine._stream_active = True
    engine._desired_streaming = True

    writer = threading.Thread(
        target=lambda: engine._write_output_frame(np.zeros((2, 2, 3), dtype=np.uint8), "working"),
        daemon=True,
    )
    writer.start()
    assert ffmpeg.restart_entered.wait(0.5)

    deactivate_stream(engine, "non-working", reason="phase_disabled")
    ffmpeg.allow_restart.set()
    writer.join(timeout=0.5)

    assert writer.is_alive() is False
    assert ffmpeg.restart_calls == 0
    assert engine._state == STATE_INACTIVE


def test_non_working_keeps_runtime_live_and_ready() -> None:
    context = _context()
    state = _healthy_state()
    context.set_resource("health_state", state)
    engine = DefaultStreamingEngine(context, start_output_loop=False)
    engine._ffmpeg = _FakeFfmpeg()
    engine._streaming_enabled_by_phase = {"non-working": False}

    status = engine.push(np.zeros((2, 2, 3), dtype=np.uint8), [], "non-working")
    snapshot = state.snapshot()
    live, _ = _evaluate_liveness(snapshot, time.time(), _probe_config())
    ready, _ = _evaluate_readiness(snapshot, time.time(), _probe_config())

    assert status.should_stream is False
    assert live is True
    assert ready is True


def test_streaming_disabled_keeps_runtime_ready() -> None:
    context = _context(streaming_enabled=False)
    state = _healthy_state()
    context.set_resource("health_state", state)
    engine = DefaultStreamingEngine(context, start_output_loop=False)
    engine._ffmpeg = _FakeFfmpeg()

    status = engine.push(np.zeros((2, 2, 3), dtype=np.uint8), [], "working")
    ready, _ = _evaluate_readiness(state.snapshot(), time.time(), _probe_config())

    assert status.should_stream is False
    assert ready is True


def test_healthz_and_readyz_use_distinct_runtime_signals() -> None:
    state = EdgeHealthState(logging.getLogger("test"))
    state.mark_startup_ok()
    state.mark_loop_tick()
    config = ProbeConfig(
        liveness_timeout_seconds=10.0,
        readiness_timeout_seconds=0.01,
        startup_grace_seconds=0.01,
    )
    now = time.time() + 0.02

    live, _ = _evaluate_liveness(state.snapshot(), now, config)
    ready, _ = _evaluate_readiness(state.snapshot(), now, config)

    assert live is True
    assert ready is False


def test_non_working_stream_snapshot_is_disabled_not_degraded() -> None:
    reporter = TaskHealthReporter(StageStats(task_name="stream"))
    status = StreamingStatus(
        queue_size=0,
        dropped_frames=0,
        processed_frames=0,
        stream_active=False,
        should_stream=False,
        phase="non-working",
        enabled=True,
        last_error=None,
        state=STATE_INACTIVE,
        reconnect_count=0,
        write_failures=0,
        no_frame_seconds=0.0,
        since_last_write_seconds=0.0,
        ffmpeg_alive=False,
    )

    snapshot = reporter.snapshot_streaming(
        frame_meta=None,
        phase="non-working",
        status=status,
        write_rate_meter=RateMeter(),
        unique_write_rate_meter=RateMeter(),
        health_threshold_seconds=1.0,
    )

    assert snapshot["state"] == "disabled"
    assert snapshot["should_stream"] is False
    assert snapshot["alive"] is False
