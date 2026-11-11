"""spec-edge.md 3.2：BaseIngestionEngine 的背景狀態（ingest-worker）。"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import numpy as np
from smart_workflow import EngineStatus

from edge.pipeline.tasks.ingestion.sources import SourceFrame
from edge.pipeline.tasks.ingestion.worker import CaptureWorker

import pytest

from test_ingestion_supervisor import FakeEngine, FakeSource, _wait_until


@pytest.fixture
def engines():
    created: list[FakeEngine] = []
    yield created
    for engine in created:
        for source in engine.created:
            source.unblock.set()
        engine.stop()


def test_status_before_start_is_inactive() -> None:
    status = FakeEngine([]).status()

    assert isinstance(status, EngineStatus)
    assert status.active is False
    assert status.alive is False
    assert status.success_count == 0


def test_each_new_frame_counts_as_success(engines) -> None:
    engine = FakeEngine([FakeSource(marker=1)])
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: engine.status().success_count >= 3)

    status = engine.status()
    assert status.active is True
    assert status.alive is True
    assert status.error_count == 0
    assert status.last_progress_at is not None


def test_read_failure_counts_as_error_even_after_next_frame(engines) -> None:
    # 讀取失敗後重連、又收到畫面時，_on_frame 會清空 _last_error，但錯誤次數必須保留。
    engine = FakeEngine([FakeSource(marker=1, frames=2, then="fail"), FakeSource(marker=2)])
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: engine.status().error_count >= 1 and engine._snapshot_latest_frame()[2] >= 4)

    status = engine.status()
    assert status.error_count == 1
    assert status.last_error_reason == "read_failed"
    assert "read failed #1" in status.last_error
    assert engine.health_snapshot()["last_error"] is None


def test_open_failure_counts_as_error(engines) -> None:
    engine = FakeEngine([FakeSource(marker=1, fail_open=True), FakeSource(marker=2)])
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: engine.status().success_count >= 1)

    assert engine.status().error_count == 1
    assert engine.status().last_error_reason == "open_failed"


def test_busy_since_covers_blocking_read(engines) -> None:
    source = FakeSource(marker=1, frames=1, then="block")
    engine = FakeEngine([source])
    engines.append(engine)
    engine._worker_config = type(engine._worker_config)(stall_seconds=30, open_timeout_seconds=30)
    engine.fetch()

    _wait_until(lambda: engine.status().busy_since is not None)

    source.unblock.set()


def test_end_of_stream_is_not_an_error_and_becomes_inactive(engines) -> None:
    engine = FakeEngine([FakeSource(marker=1, frames=3, then="eof")], reconnect_on_eof=False)
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: engine.health_snapshot()["finished"])

    status = engine.status()
    assert status.error_count == 0
    assert status.busy_since is None
    assert status.active is False
    assert status.fatal is None


def test_fatal_when_stuck_workers_reach_max(engines) -> None:
    sources = [FakeSource(marker=i, frames=1, then="block", ignore_abort=True) for i in range(1, 4)]
    engine = FakeEngine(sources)
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: engine.status().fatal is not None)

    status = engine.status()
    assert status.fatal_reason == "ingest_workers_stuck"
    assert "3" in status.fatal


def test_hooks_from_other_generation_are_ignored() -> None:
    engine = FakeEngine([])
    engine._status_generation = 2

    engine._on_read_begin(1)
    engine._on_read_end(1, "read_failed", "late failure")

    status = engine.status()
    assert status.busy_since is None
    assert status.error_count == 0


def test_retire_marks_idle_before_supervisor_replaces_worker() -> None:
    engine = FakeEngine([])
    engine._status_generation = 1
    engine._status_cell.mark_busy()
    worker = CaptureWorker(
        generation=1,
        source=FakeSource(marker=1),
        on_frame=lambda *args: None,
        frames_per_cycle=1,
        interval_fn=lambda _: 0.0,
        name="w-1",
    )
    worker.stall_reason = "test"

    engine._retire_worker(worker)

    assert engine.status().busy_since is None
    engine._on_read_begin(1)
    assert engine.status().busy_since is None


def test_worker_reports_read_hooks_in_order() -> None:
    events: list[tuple] = []
    source = FakeSource(marker=1, frames=2, then="fail")
    done = threading.Event()

    def on_frame(generation, frame, ts) -> None:
        events.append(("frame", generation))

    worker = CaptureWorker(
        generation=7,
        source=source,
        on_frame=on_frame,
        frames_per_cycle=1,
        interval_fn=lambda _: 0.0,
        name="w-7",
        on_read_begin=lambda generation: events.append(("begin", generation)),
        on_read_end=lambda generation, outcome, error: (events.append(("end", generation, outcome, error)), outcome != "frame" and done.set()),
    )
    worker.start()
    assert done.wait(2.0)
    worker.join(1.0)

    assert events == [
        ("begin", 7), ("end", 7, "frame", None), ("frame", 7),
        ("begin", 7), ("end", 7, "frame", None), ("frame", 7),
        ("begin", 7), ("end", 7, "read_failed", "read failed #1"),
    ]


def test_late_frame_from_old_generation_is_not_success(engines) -> None:
    engine = FakeEngine([FakeSource(marker=1)])
    engines.append(engine)
    engine.fetch()
    _wait_until(lambda: engine.status().success_count >= 1)
    engine.stop()
    count = engine.status().success_count

    engine._on_frame(0, SourceFrame(image=np.zeros((2, 2, 3), dtype=np.uint8)), datetime.now(timezone.utc))

    assert engine.status().success_count == count
