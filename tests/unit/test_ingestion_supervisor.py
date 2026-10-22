from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

import numpy as np
import pytest
from smart_workflow import TaskError

from edge.config import IngestionWorkerConfig
from edge.pipeline.tasks.ingestion.backoff import ReconnectBackoff
from edge.pipeline.tasks.ingestion.engines.base import BaseIngestionEngine
from edge.pipeline.tasks.ingestion.sources import FrameSource, FrameSourceError, SourceFrame

FAST = IngestionWorkerConfig(
    open_timeout_seconds=0.2,
    read_timeout_seconds=0.1,
    stall_seconds=0.1,
    reconnect_max_seconds=0.05,
    worker_stop_timeout_seconds=0.05,
    max_stuck_workers=3,
)


def _wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not met before timeout")


class FakeSource(FrameSource):
    """可設定的假來源：先送出 frames 張畫面，之後依設定 EOF、讀取失敗或阻塞。"""

    def __init__(
        self,
        *,
        marker: int = 0,
        frames: int | None = None,
        then: str = "fail",
        ignore_abort: bool = False,
        fail_open: bool = False,
        open_delay: float = 0.0,
    ) -> None:
        super().__init__()
        self._open_delay = open_delay
        self.marker = marker
        self._remaining = frames
        self._then = then
        self._ignore_abort = ignore_abort
        self._fail_open = fail_open
        self.unblock = threading.Event()
        self.closed_by: threading.Thread | None = None
        self.close_count = 0

    def open(self) -> None:
        time.sleep(self._open_delay)
        if self._fail_open:
            raise FrameSourceError(f"open failed #{self.marker}")

    def read(self) -> SourceFrame | None:
        if self._remaining is None or self._remaining > 0:
            if self._remaining is not None:
                self._remaining -= 1
            time.sleep(0.002)
            return SourceFrame(image=np.full((2, 2, 3), self.marker, dtype=np.uint8))
        if self._then == "eof":
            self.end_of_stream = True
            self.last_error = "影片播放結束，且未啟用 EDGE_FILE_LOOP"
            return None
        if self._then == "block":
            self.unblock.wait()
            return None
        self.last_error = f"read failed #{self.marker}"
        return None

    def close(self) -> None:
        self.close_count += 1
        self.closed_by = threading.current_thread()

    def abort(self) -> None:
        if not self._ignore_abort:
            self.unblock.set()


class FakeEngine(BaseIngestionEngine):
    source_label = "fake"
    _first_frame_timeout_seconds = 0.5

    def __init__(self, sources: list[FakeSource], *, reconnect_on_eof: bool = True, config=FAST) -> None:
        super().__init__(None, worker_config=config)
        self._cached_config = object()
        self._pending = list(sources)
        self.created: list[FakeSource] = []
        self._reconnect_on_eof = reconnect_on_eof

    def _build_source(self, config) -> FrameSource:
        source = self._pending.pop(0) if self._pending else FakeSource(marker=99)
        self.created.append(source)
        return source

    def _reconnect_base_seconds(self, config) -> float:
        return 0.01

    def _should_reconnect(self, exit_reason: str) -> bool:
        return self._reconnect_on_eof or exit_reason != "eof"


@pytest.fixture
def engines():
    created: list[FakeEngine] = []
    yield created
    for engine in created:
        for source in engine.created:
            source.unblock.set()
        engine.stop()


def _marker(engine: FakeEngine) -> int:
    frame, _, _ = engine._snapshot_latest_frame()
    return -1 if frame is None else int(frame[0, 0, 0])


def test_backoff_doubles_until_max_and_resets() -> None:
    backoff = ReconnectBackoff(base_seconds=0.5, max_seconds=4.0)

    assert [backoff.next_delay() for _ in range(5)] == [0.5, 1.0, 2.0, 4.0, 4.0]
    backoff.reset()
    assert backoff.next_delay() == 0.5
    assert ReconnectBackoff(base_seconds=0.0, max_seconds=4.0).next_delay() == 0.0


def test_source_is_closed_on_worker_thread(engines) -> None:
    source = FakeSource(marker=1)
    engine = FakeEngine([source])
    engines.append(engine)

    payload = engine.fetch()
    records = engine.stop()

    assert payload["source"] == "fake"
    assert int(payload["frame"][0, 0, 0]) == 1
    assert records[0]["state"] == "done"
    assert source.close_count == 1
    assert source.closed_by is not threading.current_thread()
    assert "IngestionWorker" in source.closed_by.name


def test_stop_unblocks_read_through_abort(engines) -> None:
    source = FakeSource(marker=1, frames=1, then="block")
    engine = FakeEngine([source], config=IngestionWorkerConfig(**{**FAST.__dict__, "stall_seconds": 5.0}))
    engines.append(engine)
    engine.fetch()

    records = engine.stop()

    assert records[0]["state"] == "done"
    assert source.close_count == 1
    assert "IngestionWorker" in source.closed_by.name


def test_stalled_worker_is_replaced(engines) -> None:
    first = FakeSource(marker=1, frames=3, then="block")
    second = FakeSource(marker=2)
    engine = FakeEngine([first, second])
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: _marker(engine) == 2)

    snapshot = engine.health_snapshot()
    assert snapshot["worker_generation"] == 2
    assert snapshot["reconnect_count"] == 1
    assert snapshot["read_failure_count"] >= 1
    assert snapshot["stuck_workers"] == 0
    assert first.close_count == 1
    assert second.close_count == 0


def test_stuck_worker_cannot_overwrite_new_generation(engines) -> None:
    first = FakeSource(marker=1, frames=3, then="block", ignore_abort=True)
    second = FakeSource(marker=2)
    engine = FakeEngine([first, second])
    engines.append(engine)
    engine.fetch()
    _wait_until(lambda: _marker(engine) == 2)
    assert engine.health_snapshot()["stuck_workers"] == 1

    late = SourceFrame(image=np.full((2, 2, 3), 1, dtype=np.uint8))
    engine._on_frame(1, late, datetime.now(timezone.utc))
    assert _marker(engine) == 2

    first.unblock.set()
    _wait_until(lambda: first.close_count == 1)
    assert "IngestionWorker-1" in first.closed_by.name
    assert second.close_count == 0
    _wait_until(lambda: engine.health_snapshot()["stuck_workers"] == 0)


def test_fatal_after_max_stuck_workers(engines) -> None:
    sources = [FakeSource(marker=i, frames=1, then="block", ignore_abort=True) for i in range(1, 4)]
    engine = FakeEngine(sources)
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: engine.health_snapshot()["fatal"])

    snapshot = engine.health_snapshot()
    assert snapshot["stuck_workers"] == 3
    assert snapshot["worker_alive"] is False
    assert "3" in snapshot["fatal_reason"]
    with pytest.raises(TaskError, match="fatal"):
        engine.fetch()


def test_end_of_stream_finishes_without_reconnect(engines) -> None:
    source = FakeSource(marker=1, frames=3, then="eof")
    engine = FakeEngine([source], reconnect_on_eof=False)
    engines.append(engine)
    engine.fetch()

    _wait_until(lambda: engine.health_snapshot()["finished"])

    assert engine.health_snapshot()["worker_generation"] == 1
    with pytest.raises(TaskError, match="影片播放結束，且未啟用 EDGE_FILE_LOOP"):
        engine.fetch()
    assert engine.health_snapshot()["fatal"] is False


def test_open_failures_retry_with_backoff(engines) -> None:
    engine = FakeEngine([FakeSource(marker=1, fail_open=True), FakeSource(marker=2, fail_open=True), FakeSource(marker=3)])
    engines.append(engine)

    payload = engine.fetch()

    assert int(payload["frame"][0, 0, 0]) == 3
    snapshot = engine.health_snapshot()
    assert snapshot["worker_generation"] == 3
    assert snapshot["last_error"] is None
    assert snapshot["reconnect_count"] == 1


def test_fetch_without_frames_reports_last_error(engines) -> None:
    engine = FakeEngine([FakeSource(marker=i, fail_open=True) for i in range(1, 100)])
    engines.append(engine)

    with pytest.raises(TaskError, match="open failed"):
        engine.fetch()
    assert engine.health_snapshot()["worker_alive"] is True


def test_stop_then_start_uses_new_supervisor(engines) -> None:
    engine = FakeEngine([FakeSource(marker=1), FakeSource(marker=2)])
    engines.append(engine)
    engine.fetch()
    engine.stop()
    assert engine.is_started() is False

    payload = engine.fetch()

    assert int(payload["frame"][0, 0, 0]) == 2
    assert engine.is_started() is True


def test_first_fetch_waits_for_slow_open(engines) -> None:
    # 舊版 start() 同步開啟後才開始等第一張畫面；改成背景開啟後，第一次等待必須包含 open timeout。
    config = IngestionWorkerConfig(**{**FAST.__dict__, "open_timeout_seconds": 1.0})
    engine = FakeEngine([FakeSource(marker=1, open_delay=0.8)], config=config)
    engines.append(engine)

    payload = engine.fetch()

    assert int(payload["frame"][0, 0, 0]) == 1
    assert engine.health_snapshot()["worker_generation"] == 1
