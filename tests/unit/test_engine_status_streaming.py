"""spec-edge.md 2、3.2：BaseStreamingEngine 的背景狀態（stream-output）與 sink_connected()。"""
from __future__ import annotations

import numpy as np
from smart_workflow import EngineStatus

from edge.pipeline.tasks.streaming.engines.default import DefaultStreamingEngine
from edge.pipeline.tasks.streaming.engines.policy import deactivate_stream

from edge_health_testkit import FakeFfmpeg, context, streaming_config

FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


def _engine(*, fail: bool = False, start_output_loop: bool = False) -> DefaultStreamingEngine:
    engine = DefaultStreamingEngine(context(streaming_config()), start_output_loop=start_output_loop)
    engine._ffmpeg = FakeFfmpeg(fail=fail)
    return engine


def _streaming(engine: DefaultStreamingEngine) -> DefaultStreamingEngine:
    engine.push(FRAME, [], "working")
    return engine


def test_status_is_inactive_until_streaming_is_desired() -> None:
    status = _engine().status()

    assert isinstance(status, EngineStatus)
    assert status.active is False
    assert status.success_count == 0


def test_active_follows_desired_streaming_and_alive_follows_thread() -> None:
    engine = _engine(start_output_loop=True)
    try:
        _streaming(engine)
        assert engine.status().active is True
        assert engine.status().alive is True

        engine._streaming_enabled_by_phase = {"off": False}
        engine.push(FRAME, [], "off")
        assert engine.status().active is False
    finally:
        engine.close()
    assert engine.status().alive is False


def test_successful_write_counts_as_success() -> None:
    engine = _streaming(_engine())

    engine._write_output_frame(FRAME, "working")

    status = engine.status()
    assert status.success_count == 1
    assert status.busy_since is None
    assert status.last_progress_at is not None


def test_busy_since_covers_the_write_call() -> None:
    engine = _streaming(_engine())
    seen: list[object] = []
    engine._ffmpeg.on_write = lambda: seen.append(engine.status().busy_since)

    engine._write_output_frame(FRAME, "working")

    assert seen and seen[0] is not None
    assert engine.status().busy_since is None


def test_failed_write_counts_as_error() -> None:
    engine = _streaming(_engine(fail=True))

    engine._write_output_frame(FRAME, "working")
    engine._write_output_frame(FRAME, "working")

    status = engine.status()
    assert status.error_count == 2
    assert status.last_error_reason == "write_failed"
    assert status.last_error == "broken pipe"
    assert status.busy_since is None


def test_write_completed_outside_desired_phase_is_neither_success_nor_error() -> None:
    engine = _streaming(_engine())
    engine._ffmpeg.on_write = lambda: deactivate_stream(engine, "off", reason="phase_disabled")

    engine._write_output_frame(FRAME, "working")

    status = engine.status()
    assert (status.success_count, status.error_count, status.busy_since) == (0, 0, None)


def test_write_failure_after_deactivation_is_not_an_error() -> None:
    engine = _streaming(_engine(fail=True))
    engine._ffmpeg.on_write = lambda: deactivate_stream(engine, "off", reason="phase_disabled")

    engine._write_output_frame(FRAME, "working")

    status = engine.status()
    assert (status.success_count, status.error_count, status.busy_since) == (0, 0, None)


def test_sink_connected_is_none_when_not_streaming() -> None:
    engine = _engine()
    assert engine.sink_connected() is None

    engine._streaming_enabled_by_phase = {"off": False}
    engine.push(FRAME, [], "off")
    assert engine.sink_connected() is None


def test_sink_connected_is_none_without_frame_or_before_first_write() -> None:
    engine = _engine()
    engine.push(None, [], "working")
    assert engine.sink_connected() is None

    engine.push(FRAME, [], "working")
    assert engine.sink_connected() is None


def test_sink_connected_follows_last_write() -> None:
    engine = _streaming(_engine())

    engine._write_output_frame(FRAME, "working")
    assert engine.sink_connected() is True

    engine._ffmpeg.fail = True
    engine._write_output_frame(FRAME, "working")
    assert engine.sink_connected() is False
