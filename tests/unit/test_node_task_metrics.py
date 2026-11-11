"""spec-edge.md 9.2：舊機制的資訊改用 smart_health metric 記錄（ingest、publish、stream）。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from smart_workflow import HealthRuntime, TaskContext, TaskError

from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.ingestion.engines import BaseIngestionEngine
from edge.pipeline.tasks.publish.engine import BasePublishEngine, PublishOutcome
from edge.pipeline.tasks.publish.task import PublishResultTask
from edge.pipeline.tasks.streaming.engines.default import DefaultStreamingEngine
from edge.pipeline.tasks.streaming.task import StreamingTask
from edge.schema import FrameMeta

from edge_health_testkit import FakeFfmpeg, Monitor, streaming_config
from test_ingestion_supervisor import FakeEngine, FakeSource, _marker, _wait_until

FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


class FakeRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _record(*args):
            self.calls.append((name, *args))

        return _record

    def metrics(self) -> dict:
        return {call[1]: call[2] for call in self.calls if call[0] == "set_metric"}


class FakeMonitor:
    def __init__(self) -> None:
        self.recorders: dict[str, FakeRecorder] = {}

    def recorder(self, node_id: str) -> FakeRecorder:
        return self.recorders.setdefault(node_id, FakeRecorder())


def _context(config=None) -> tuple[TaskContext, FakeMonitor]:
    monitor = FakeMonitor()
    config = config or SimpleNamespace()
    for key, value in {
        "camera": SimpleNamespace(camera_id="cam-1"),
        "phase_messaging": SimpleNamespace(resource_name="edge_mode"),
        "health_report_interval_seconds": 5.0,
    }.items():
        if not hasattr(config, key):
            setattr(config, key, value)
    health = HealthRuntime(monitor, clock=lambda: 1000.0)
    return TaskContext(logger=logging.getLogger("test"), config=config, monitor=Monitor(), health=health), monitor


def _stage(context: TaskContext, *, is_new: bool, seq: int = 1) -> None:
    context.set_resource("decoded_frame", FRAME)
    context.set_resource(
        "frame_meta", FrameMeta(session_id="s", frame_seq=seq, capture_ts=datetime(2026, 1, 1, tzinfo=timezone.utc))
    )
    context.set_resource("pipeline_frame_is_new", is_new)


# ---------------------------------------------------------------- ingest


class CountingIngestion(BaseIngestionEngine):
    """計數器由測試直接設定；fetch 依設定回傳畫面或拋出 TaskError。"""

    source_label = "counting"

    def __init__(self) -> None:
        super().__init__(None)
        self.fail = False
        self.seq = 0

    def fetch(self) -> dict:
        if self.fail:
            raise TaskError("counting 無法取得影格")
        self.seq += 1
        return {"frame": FRAME, "source": "counting", "capture_ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "frame_seq": self.seq}


def _ingestion_task(engine) -> IngestionTask:
    task = IngestionTask()
    task._mode = "file"
    task._engine = engine
    return task


def test_ingest_metrics_follow_engine_counters() -> None:
    context, monitor = _context(SimpleNamespace(ingestion=None))
    engine = CountingIngestion()
    engine._reconnect_count = 2
    engine._read_failure_count = 5
    task = _ingestion_task(engine)

    task.execute(context)

    metrics = monitor.recorders["ingest"].metrics()
    assert (metrics["reconnects"], metrics["read_failures"]) == (2, 5)


def test_ingest_metrics_written_when_fetch_raises() -> None:
    context, monitor = _context(SimpleNamespace(ingestion=None))
    engine = CountingIngestion()
    engine.fail = True
    engine._read_failure_count = 3
    task = _ingestion_task(engine)

    with pytest.raises(TaskError):
        task.execute(context)

    metrics = monitor.recorders["ingest"].metrics()
    assert (metrics["reconnects"], metrics["read_failures"]) == (0, 3)


def test_ingestion_engine_counts_reconnect_and_read_failure() -> None:
    engine = FakeEngine([FakeSource(marker=1, frames=3, then="fail"), FakeSource(marker=2)])
    try:
        assert (engine.reconnect_count, engine.read_failure_count) == (0, 0)
        engine.fetch()
        _wait_until(lambda: _marker(engine) == 2)

        snapshot = engine.health_snapshot()
        assert engine.reconnect_count == 1
        assert engine.read_failure_count >= 1
        assert snapshot["reconnect_count"] == engine.reconnect_count
        assert snapshot["read_failure_count"] == engine.read_failure_count
    finally:
        for source in engine.created:
            source.unblock.set()
        engine.stop()


# ---------------------------------------------------------------- publish


class OutcomePublish(BasePublishEngine):
    def __init__(self, outcome: PublishOutcome) -> None:
        self.outcome = outcome

    def publish(self, detections, *, models_run=None, models_reuse=None, frame_meta=None):
        return self.outcome


def _publish(outcome: PublishOutcome) -> PublishResultTask:
    task = PublishResultTask(None)
    task._enabled = True
    task._engine = OutcomePublish(outcome)
    return task


@pytest.mark.parametrize("outcome", [PublishOutcome(3, 200), PublishOutcome(3, None, result="failed", reason="x")])
def test_publish_metric_is_published_count(outcome: PublishOutcome) -> None:
    context, monitor = _context()
    _stage(context, is_new=True)

    _publish(outcome).execute(context)

    assert monitor.recorders["publish"].metrics()["published"] == 3


def test_publish_metric_not_written_on_skip() -> None:
    context, monitor = _context()
    _stage(context, is_new=False)

    assert _publish(PublishOutcome(3, 200)).execute(context).status == "skipped"

    assert "published" not in monitor.recorders["publish"].metrics()


# ---------------------------------------------------------------- stream


def _streaming_task(context: TaskContext, *, fail: bool = False) -> StreamingTask:
    task = StreamingTask(None)
    task._enabled = True
    engine = DefaultStreamingEngine(context, start_output_loop=False)
    engine._ffmpeg = FakeFfmpeg(fail=fail)
    task._engine = engine
    return task


def test_streaming_engine_counters() -> None:
    context, _ = _context(streaming_config())
    engine = _streaming_task(context, fail=True).engine
    engine.push(FRAME, [], "working")
    assert (engine.ffmpeg_restarts, engine.write_failures) == (0, 0)

    engine._write_output_frame(FRAME, "working")

    assert engine.write_failures == 1
    assert engine.ffmpeg_restarts == 1
    assert engine._ffmpeg.restart_calls == 1


def test_stream_metrics_follow_engine_counters() -> None:
    context, monitor = _context(streaming_config())
    task = _streaming_task(context)
    task.engine._reconnect_count = 4
    task.engine._write_failures = 7
    _stage(context, is_new=True)

    task.execute(context)

    metrics = monitor.recorders["stream"].metrics()
    assert (metrics["ffmpeg_restarts"], metrics["write_failures"]) == (4, 7)


# ---------------------------------------------------------------- 自訂 Engine


class BareIngestion:
    """不繼承 BaseIngestionEngine 的自訂 Engine：沒有計數器屬性。"""

    source_label = "bare"

    def fetch(self) -> dict:
        return {"frame": FRAME, "source": "bare", "capture_ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "frame_seq": 1}

    def health_snapshot(self) -> dict:
        return {"worker_alive": True}

    def is_started(self) -> bool:
        return True


def test_metrics_skipped_for_engine_without_counters() -> None:
    context, monitor = _context(SimpleNamespace(ingestion=None))
    task = _ingestion_task(BareIngestion())

    assert task.execute(context).status == "success"

    metrics = monitor.recorders["ingest"].metrics()
    assert "reconnects" not in metrics and "read_failures" not in metrics
