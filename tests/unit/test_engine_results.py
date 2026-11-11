"""spec-edge.md 3.1、3.3：Engine 的統一結果與明確無法恢復的推理錯誤。"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from smart_workflow import EngineResult, FatalTaskError, TaskError

from edge.pipeline.tasks.inference.engine import BaseInferenceEngine, DefaultInferenceEngine, InferenceOutcome
from edge.pipeline.tasks.inference.errors import CUDA_UNRECOVERABLE_MARKERS
from edge.pipeline.tasks.inference.scheduled_engine.engine import ScheduledInferenceEngine
from edge.pipeline.tasks.inference.scheduled_engine.models import ScheduledModelTask
from edge.pipeline.tasks.inference.task import InferenceTask
from edge.pipeline.tasks.publish.engine import DefaultPublishEngine, MessagingPublishEngine, PublishOutcome
from edge.pipeline.tasks.streaming.engines.default import DefaultStreamingEngine
from edge.pipeline.tasks.streaming.types import StreamingStatus
from edge.messaging import MESSAGING_CLIENT_RESOURCE

from edge_health_testkit import FakeFfmpeg, context, streaming_config

FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


# ---------------------------------------------------------------- result types


def test_result_types_inherit_engine_result() -> None:
    for cls in (InferenceOutcome, StreamingStatus, PublishOutcome):
        assert issubclass(cls, EngineResult)


def test_publish_outcome_keeps_existing_construction() -> None:
    outcome = PublishOutcome(published=8, status=200)
    positional = PublishOutcome(3, None)

    assert (outcome.published, outcome.status, outcome.result) == (8, 200, "success")
    assert (positional.published, positional.status) == (3, None)


def test_inference_outcome_keeps_existing_construction() -> None:
    outcome = InferenceOutcome([], ["a"], ["b"])

    assert (outcome.models_run, outcome.models_reuse, outcome.result) == (["a"], ["b"], "success")


def test_streaming_status_to_dict_is_unchanged() -> None:
    status = StreamingStatus(0, 0, 0, False, False, "p", True, result="skipped", reason="phase_disabled")

    assert "result" not in status.to_dict()
    assert "reason" not in status.to_dict()


# ---------------------------------------------------------------- inference


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, frame, metadata):
        self.calls += 1
        return []


def _scheduled_engine(tasks_by_phase) -> ScheduledInferenceEngine:
    engine = ScheduledInferenceEngine.__new__(ScheduledInferenceEngine)
    BaseInferenceEngine.__init__(engine, None)
    engine._camera_config = None
    engine._tasks_by_phase = tasks_by_phase
    engine._active_phase = None
    engine._cached_results = {}
    engine._forklift_source_tasks = []
    engine._forklift_class_names = []
    engine._forklift_min_score = 0.4
    engine._forklift_active_hold = 10.0
    engine._forklift_idle_seconds = 10.0
    engine._forklift_last_seen_ts = None
    engine._forklift_active = False
    engine._forklift_idle = True
    engine._forklift_idle_since_ts = None
    return engine


def test_scheduled_inference_success_includes_no_detections() -> None:
    engine = _scheduled_engine({"p": [ScheduledModelTask("m", "every_frame", 0.0, _Model())]})

    outcome = engine.process(FRAME, phase="p")

    assert outcome.detections == []
    assert outcome.result == "success"


def test_scheduled_inference_success_when_only_reusing_results() -> None:
    task = ScheduledModelTask("m", "interval", 3600.0, _Model())
    engine = _scheduled_engine({"p": [task]})
    engine.process(FRAME, phase="p")

    outcome = engine.process(FRAME, phase="p")

    assert outcome.models_run == []
    assert outcome.result == "success"


@pytest.mark.parametrize("tasks_by_phase", [{}, {"p": []}])
def test_scheduled_inference_skipped_when_phase_has_no_tasks(tasks_by_phase) -> None:
    outcome = _scheduled_engine(tasks_by_phase).process(FRAME, phase="p")

    assert (outcome.result, outcome.reason) == ("skipped", "phase_disabled")


def test_default_inference_skipped_without_frame() -> None:
    engine = DefaultInferenceEngine(None)

    outcome = engine.process(None)

    assert (outcome.result, outcome.reason) == ("skipped", "no_frame")


class _RaisingEngine(BaseInferenceEngine):
    def __init__(self, exc: Exception) -> None:
        super().__init__(None)
        self.exc = exc

    def process(self, frame, *, phase=None, metadata=None):
        raise self.exc


def _inference_context():
    config = SimpleNamespace(
        camera=SimpleNamespace(camera_id="cam-1"),
        phase_messaging=SimpleNamespace(resource_name="edge_mode"),
        health_report_interval_seconds=5.0,
        health_stale_threshold_seconds=5.0,
    )
    ctx = context(config)
    ctx.set_resource("decoded_frame", FRAME)
    ctx.set_resource("pipeline_frame_is_new", True)
    return ctx


def test_cuda_marker_list_is_exact() -> None:
    assert CUDA_UNRECOVERABLE_MARKERS == (
        "illegal memory access",
        "unspecified launch failure",
        "device-side assert triggered",
    )


@pytest.mark.parametrize("marker", list(CUDA_UNRECOVERABLE_MARKERS))
def test_cuda_errors_raise_fatal_task_error(marker: str) -> None:
    task = InferenceTask(None)
    original = RuntimeError(f"CUDA error: an {marker} was encountered")
    task._engine = _RaisingEngine(original)

    with pytest.raises(FatalTaskError) as excinfo:
        task.run(_inference_context())

    assert excinfo.value.reason == "cuda_unrecoverable"
    assert marker in str(excinfo.value.message)
    assert excinfo.value.__cause__ is original


@pytest.mark.parametrize("exc", [RuntimeError("CUDA out of memory"), TaskError("model missing"), ValueError("bad")])
def test_other_inference_errors_are_raised_unchanged(exc: Exception) -> None:
    task = InferenceTask(None)
    task._engine = _RaisingEngine(exc)

    with pytest.raises(type(exc)) as excinfo:
        task.run(_inference_context())

    assert excinfo.value is exc
    assert not isinstance(excinfo.value, FatalTaskError)


# ---------------------------------------------------------------- streaming


def _streaming_engine(**kwargs) -> DefaultStreamingEngine:
    engine = DefaultStreamingEngine(context(streaming_config(**kwargs)), start_output_loop=False)
    engine._ffmpeg = FakeFfmpeg()
    return engine


def test_streaming_success_when_frame_handed_to_output_thread() -> None:
    engine = _streaming_engine()

    status = engine.push(FRAME, [], "working")

    assert status.result == "success"
    assert engine._get_latest_packet() is not None


def test_streaming_skipped_outside_streaming_phase() -> None:
    engine = _streaming_engine()
    engine._streaming_enabled_by_phase = {"off": False}

    status = engine.push(FRAME, [], "off")

    assert (status.result, status.reason) == ("skipped", "phase_disabled")


def test_streaming_skipped_when_url_missing() -> None:
    status = _streaming_engine(url="").push(FRAME, [], "working")

    assert (status.result, status.reason) == ("skipped", "stream_unavailable")


def test_streaming_skipped_without_frame() -> None:
    status = _streaming_engine().push(None, [], "working")

    assert (status.result, status.reason) == ("skipped", "no_frame")


# ---------------------------------------------------------------- publish


class _Client:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    def publish(self, route_key, payload) -> bool:
        return self.ok


def _publish_engine(ok: bool) -> MessagingPublishEngine:
    ctx = context(SimpleNamespace(integration=None, camera=SimpleNamespace(camera_id="cam-1")))
    ctx.set_resource(MESSAGING_CLIENT_RESOURCE, _Client(ok))
    return MessagingPublishEngine(ctx)


def test_messaging_publish_success() -> None:
    outcome = _publish_engine(True).publish([])

    assert (outcome.result, outcome.status) == ("success", 200)


def test_messaging_publish_failure_is_failed_not_raised() -> None:
    outcome = _publish_engine(False).publish([])

    assert (outcome.result, outcome.reason, outcome.status) == ("failed", "publish_failed", None)


def test_rest_publish_failure_is_failed(monkeypatch) -> None:
    import urllib.error
    import urllib.request

    def _fail(*args, **kwargs):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", _fail)
    ctx = context(
        SimpleNamespace(
            integration=SimpleNamespace(api_base="http://127.0.0.1:9", timeout_seconds=1),
            camera=SimpleNamespace(camera_id="cam-1"),
        )
    )

    outcome = DefaultPublishEngine(ctx).publish([])

    assert (outcome.result, outcome.reason, outcome.status) == ("failed", "publish_failed", None)
