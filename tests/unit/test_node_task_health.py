"""spec-edge.md 第 4 節：NodeTask 的 health_node、結果對應與 metrics。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from smart_workflow import HealthRuntime, TaskContext

from edge.pipeline.pipeline import InitPipelineTask, PipelineScheduler
from edge.pipeline.tasks.inference.engine import BaseInferenceEngine, InferenceOutcome
from edge.pipeline.tasks.inference.task import InferenceTask
from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.ingestion.engines import BaseIngestionEngine
from edge.pipeline.tasks.publish.engine import BasePublishEngine, PublishOutcome
from edge.pipeline.tasks.publish.task import PublishResultTask
from edge.pipeline.tasks.streaming.engines.default import DefaultStreamingEngine
from edge.pipeline.tasks.streaming.task import StreamingTask
from edge.schema import FrameMeta

from edge_health_testkit import FakeFfmpeg, Monitor, streaming_config

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


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _context(config=None, *, clock=None) -> tuple[TaskContext, FakeMonitor]:
    monitor = FakeMonitor()
    config = config or SimpleNamespace()
    defaults = {
        "camera": SimpleNamespace(camera_id="cam-1"),
        "phase_messaging": SimpleNamespace(resource_name="edge_mode"),
        "health_report_interval_seconds": 5.0,
        "health_stale_threshold_seconds": 5.0,
    }
    for key, value in defaults.items():
        if not hasattr(config, key):
            setattr(config, key, value)
    health = HealthRuntime(monitor, clock=clock or Clock())
    return TaskContext(logger=logging.getLogger("test"), config=config, monitor=Monitor(), health=health), monitor


def _frame_meta(seq: int = 1, capture_monotonic: float | None = None) -> FrameMeta:
    return FrameMeta(
        session_id="s",
        frame_seq=seq,
        capture_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        capture_monotonic=capture_monotonic,
    )


def _stage(context: TaskContext, *, is_new: bool, meta: FrameMeta | None = None) -> None:
    context.set_resource("decoded_frame", FRAME)
    context.set_resource("frame_meta", meta or _frame_meta())
    context.set_resource("pipeline_frame_is_new", is_new)


def test_health_nodes_are_declared() -> None:
    assert IngestionTask.health_node == "ingest"
    assert InferenceTask.health_node == "infer"
    assert StreamingTask.health_node == "stream"
    assert PublishResultTask.health_node == "publish"
    assert PipelineScheduler.health_node is None
    assert InitPipelineTask.health_node is None


# ---------------------------------------------------------------- ingestion


class ScriptedIngestion(BaseIngestionEngine):
    source_label = "scripted"

    def __init__(self, seqs: list[int]) -> None:
        super().__init__(None)
        self._seqs = list(seqs)

    def fetch(self) -> dict:
        seq = self._seqs.pop(0)
        return {
            "frame": FRAME,
            "source": "scripted",
            "capture_ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "frame_seq": seq,
            "capture_monotonic": 500.0 + seq,
        }


def _ingestion_task(seqs: list[int]) -> IngestionTask:
    task = IngestionTask()
    task._mode = "file"
    task._engine = ScriptedIngestion(seqs)
    return task


def test_ingestion_new_frame_is_success_and_repeated_frame_is_skipped() -> None:
    context, monitor = _context(SimpleNamespace(ingestion=None))
    task = _ingestion_task([1, 1, 2])

    results = [task.execute(context) for _ in range(3)]

    assert [r.status for r in results] == ["success", "skipped", "success"]
    assert results[1].reason == "no_new_frame"
    names = [call[0] for call in monitor.recorders["ingest"].calls]
    assert names.count("record_success") == 2
    assert names.count("record_skip") == 1


def test_ingestion_keeps_payload_and_sets_capture_monotonic() -> None:
    context, _ = _context(SimpleNamespace(ingestion=None))
    task = _ingestion_task([3])

    result = task.execute(context)

    assert "capture_monotonic" not in result.payload
    assert result.payload["frame_meta"] == {"session_id": task._session_id, "frame_seq": 3, "capture_ts": "2026-01-01T00:00:00+00:00"}
    assert context.get_resource("frame_meta").capture_monotonic == 503.0


def test_frame_meta_capture_monotonic_is_not_sent_or_compared() -> None:
    a = _frame_meta(1, capture_monotonic=1.0)
    b = _frame_meta(1, capture_monotonic=2.0)

    assert a == b
    assert "capture_monotonic" not in a.to_dict()


def test_engine_fetch_includes_capture_monotonic() -> None:
    engine = BaseIngestionEngine(None)
    engine._cached_config = object()
    engine.start = lambda: None
    engine._frame_ready.set()
    engine._latest_frame = FRAME
    engine._latest_frame_seq = 4
    engine._latest_capture_monotonic = 123.0

    payload = engine.fetch()

    assert payload["capture_monotonic"] == 123.0
    assert set(payload) == {"frame", "source", "capture_ts", "frame_seq", "capture_monotonic"}


# ---------------------------------------------------------------- inference


class OutcomeEngine(BaseInferenceEngine):
    def __init__(self, outcome: InferenceOutcome) -> None:
        super().__init__(None)
        self.outcome = outcome
        self.calls = 0

    def process(self, frame, *, phase=None, metadata=None):
        self.calls += 1
        return self.outcome


def _inference(outcome: InferenceOutcome) -> InferenceTask:
    task = InferenceTask(None)
    task._engine = OutcomeEngine(outcome)
    return task


def test_inference_stale_frame_is_skipped_without_running_engine() -> None:
    context, monitor = _context()
    _stage(context, is_new=False)
    task = _inference(InferenceOutcome(detections=[]))

    result = task.execute(context)

    assert (result.status, result.reason) == ("skipped", "stale_frame")
    assert task._engine.calls == 0
    assert "detections" not in monitor.recorders["infer"].metrics()


def test_inference_success_sets_detection_metric() -> None:
    context, monitor = _context()
    _stage(context, is_new=True)
    detections = [SimpleNamespace(), SimpleNamespace(), SimpleNamespace()]
    task = _inference(InferenceOutcome(detections=detections, models_run=["m"]))

    result = task.execute(context)

    assert result.status == "success"
    assert result.payload["detections"] == detections
    assert monitor.recorders["infer"].metrics()["detections"] == 3


def test_inference_engine_skipped_is_mapped() -> None:
    context, _ = _context()
    _stage(context, is_new=True)

    result = _inference(InferenceOutcome(detections=[], result="skipped", reason="phase_disabled")).execute(context)

    assert (result.status, result.reason) == ("skipped", "phase_disabled")


def test_inference_engine_without_result_field_counts_as_success() -> None:
    context, _ = _context()
    _stage(context, is_new=True)
    task = InferenceTask(None)
    task._engine = OutcomeEngine(SimpleNamespace(detections=[], models_run=[], models_reuse=[]))

    assert task.execute(context).status == "success"


# ---------------------------------------------------------------- streaming


def _streaming_task(*, enabled: bool = True) -> tuple[StreamingTask, TaskContext]:
    context, _ = _context(streaming_config(enabled=enabled))
    task = StreamingTask(None)
    task._enabled = enabled
    if enabled:
        engine = DefaultStreamingEngine(context, start_output_loop=False)
        engine._ffmpeg = FakeFfmpeg()
        task._engine = engine
    return task, context


def test_streaming_disabled_is_skipped() -> None:
    task, context = _streaming_task(enabled=False)
    _stage(context, is_new=True)

    result = task.execute(context)

    assert (result.status, result.reason) == ("skipped", "stream_disabled")


def test_streaming_stale_frame_still_pushes_but_is_skipped() -> None:
    task, context = _streaming_task()
    _stage(context, is_new=False)

    result = task.execute(context)

    assert (result.status, result.reason) == ("skipped", "stale_frame")
    assert task._engine._get_latest_packet() is not None
    assert context.get_resource("streaming_status")["should_stream"] is True


def test_streaming_new_frame_maps_engine_result() -> None:
    task, context = _streaming_task()
    _stage(context, is_new=True)

    assert task.execute(context).status == "success"

    task._engine._streaming_enabled_by_phase = {"off": False}
    context.set_resource("edge_mode", "off")
    result = task.execute(context)
    assert (result.status, result.reason) == ("skipped", "phase_disabled")


# ---------------------------------------------------------------- publish


class OutcomePublish(BasePublishEngine):
    def __init__(self, outcome: PublishOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def publish(self, detections, *, models_run=None, models_reuse=None, frame_meta=None):
        self.calls += 1
        return self.outcome


def _publish(outcome: PublishOutcome, *, enabled: bool = True) -> PublishResultTask:
    task = PublishResultTask(None)
    task._enabled = enabled
    task._engine = OutcomePublish(outcome) if enabled else None
    return task


def test_publish_disabled_and_stale_are_skipped() -> None:
    context, _ = _context()
    _stage(context, is_new=True)
    assert _publish(PublishOutcome(1, 200), enabled=False).execute(context).reason == "publish_disabled"

    _stage(context, is_new=False)
    task = _publish(PublishOutcome(1, 200))
    result = task.execute(context)
    assert (result.status, result.reason) == ("skipped", "stale_frame")
    assert task._engine.calls == 0


def test_publish_failure_is_failed_and_does_not_raise() -> None:
    context, monitor = _context()
    _stage(context, is_new=True)

    result = _publish(PublishOutcome(2, None, result="failed", reason="publish_failed")).execute(context)

    assert (result.status, result.reason) == ("failed", "publish_failed")
    assert result.payload["status"] is None
    assert ("record_error", "publish_failed", None) in monitor.recorders["publish"].calls
    assert "capture_age_ms" not in monitor.recorders["publish"].metrics()


def test_publish_success_sets_capture_age_metrics_per_interval() -> None:
    clock = Clock(1000.0)
    context, monitor = _context(clock=clock)
    task = _publish(PublishOutcome(1, 200))

    def publish_at(now: float, captured: float, seq: int) -> dict:
        clock.now = now
        _stage(context, is_new=True, meta=_frame_meta(seq, capture_monotonic=captured))
        assert task.execute(context).status == "success"
        return monitor.recorders["publish"].metrics()

    first = publish_at(1000.0, 999.9, 1)
    assert first["capture_age_ms"] == pytest.approx(100.0)
    assert first["capture_age_ms_max"] == pytest.approx(100.0)

    second = publish_at(1002.0, 1001.7, 2)
    assert second["capture_age_ms"] == pytest.approx(300.0)
    assert second["capture_age_ms_max"] == pytest.approx(300.0)

    third = publish_at(1004.0, 1003.95, 3)
    assert third["capture_age_ms"] == pytest.approx(50.0)
    assert third["capture_age_ms_max"] == pytest.approx(300.0)

    # 5 秒的區間結束後重新計算。
    fourth = publish_at(1005.5, 1005.4, 4)
    assert fourth["capture_age_ms"] == pytest.approx(100.0)
    assert fourth["capture_age_ms_max"] == pytest.approx(100.0)


def test_publish_without_capture_monotonic_sets_no_age_metric() -> None:
    context, monitor = _context()
    _stage(context, is_new=True, meta=_frame_meta(1, capture_monotonic=None))

    assert _publish(PublishOutcome(1, 200)).execute(context).status == "success"
    assert "capture_age_ms" not in monitor.recorders["publish"].metrics()


# ---------------------------------------------------------------- topology helpers


def test_phase_and_activity_queries_for_topology() -> None:
    task, context = _streaming_task()
    task._engine._streaming_enabled_by_phase = {"off": False}
    assert task.engine is task._engine
    assert task.engine.wants_stream_for("working") is True
    assert task.engine.wants_stream_for("off") is False
    context.set_resource("edge_mode", "off")
    assert task.current_phase(context) == "off"

    inference = _inference(InferenceOutcome(detections=[]))
    context.set_resource("edge_mode", "beta")
    assert inference.current_phase(context) == "beta"
    assert inference.engine.has_work_for("beta") is True
    assert _publish(PublishOutcome(1, 200), enabled=False).enabled is False
