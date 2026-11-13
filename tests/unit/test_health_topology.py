"""健康架構定義：節點、lane、上游、依賴與啟用條件。"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from smart_workflow import TaskContext

from edge.messaging import EDGE_EVENTS_ROUTE, MESSAGING_CLIENT_RESOURCE
from edge.pipeline.pipeline import EdgePipeline
from edge.pipeline.tasks.inference.task import InferenceTask
from edge.pipeline.tasks.streaming.engines.default import DefaultStreamingEngine
from edge.pipeline.tasks.streaming.task import StreamingTask
from edge.runtime.health_settings import HealthSettings
from edge.runtime.health_topology import build_health_monitor, derive_stale_after, node_definitions

from edge_health_testkit import FakeFfmpeg, Monitor, streaming_config


def _config(*, poll: float = 5.0, publish: bool = True, camera: str = "cam07") -> SimpleNamespace:
    config = streaming_config()
    config.camera = SimpleNamespace(camera_id=camera)
    config.poll_interval = poll
    config.publish = SimpleNamespace(enabled=publish)
    config.phase_messaging = SimpleNamespace(resource_name="edge_mode")
    return config


def _context(config=None) -> TaskContext:
    return TaskContext(logger=logging.getLogger("test"), config=config or _config(), monitor=Monitor())


@pytest.mark.parametrize(
    ("poll", "override", "expected"),
    [(5.0, None, 15.0), (1.0, None, 11.0), (0.0, None, 11.0), (2.5, None, 11.0), (0.0, 12.0, 12.0), (5.0, 3.0, 3.0)],
)
def test_derive_stale_after(poll: float, override: float | None, expected: float) -> None:
    assert derive_stale_after(poll, override) == expected


def test_node_definitions_follow_spec_table() -> None:
    nodes = {node.node_id: node for node in node_definitions(_context(), HealthSettings())}

    assert list(nodes) == ["ingest-worker", "ingest", "infer", "stream", "stream-output", "publish"]
    table = {
        node_id: (n.lane, n.upstream, tuple(n.dependencies), n.stale_after_s, n.stuck_after_s, n.stale_reason)
        for node_id, n in nodes.items()
    }
    assert table == {
        "ingest-worker": ("ingest-bg", (), (), 5.0, 20.0, "no_new_frame"),
        "ingest": ("main", ("ingest-worker",), (), 15.0, 20.0, "stale_success"),
        "infer": ("main", ("ingest",), (), 15.0, 10.0, "stale_success"),
        "stream": ("main", ("infer",), (), 15.0, 5.0, "stale_success"),
        "stream-output": ("stream-bg", ("stream",), ("stream_sink",), 5.0, 10.0, "stale_success"),
        "publish": ("main", ("infer",), ("messaging",), 15.0, 10.0, "stale_success"),
    }
    assert all(n.startup_grace_s == 30.0 and n.error_window_s == 30.0 for n in nodes.values())


def test_override_replaces_only_derived_thresholds() -> None:
    nodes = {n.node_id: n for n in node_definitions(_context(), HealthSettings(stale_after_override=9.0))}

    assert [nodes[i].stale_after_s for i in ("ingest", "infer", "stream", "publish")] == [9.0] * 4
    assert nodes["ingest-worker"].stale_after_s == 5.0
    assert nodes["stream-output"].stale_after_s == 5.0


def test_monitor_topology() -> None:
    monitor = build_health_monitor(_context(), HealthSettings())
    monitor.evaluate_once()

    topology = monitor.topology()
    assert (topology["service"], topology["instance"]) == ("edge", "cam07")
    lanes = {lane["id"]: (lane["sequential"], lane["nodes"]) for lane in topology["lanes"]}
    assert lanes == {
        "main": (True, ["ingest", "infer", "stream", "publish"]),
        "ingest-bg": (False, ["ingest-worker"]),
        "stream-bg": (False, ["stream-output"]),
    }
    status = monitor.status()
    assert status["ready_at"] is None  # wait_for_ready=True：就緒之前不評估節點


def _callables(context: TaskContext) -> dict:
    nodes = {n.node_id: n for n in node_definitions(context, HealthSettings())}
    return {
        "infer": nodes["infer"].enabled,
        "stream": nodes["stream"].enabled,
        "stream-output": nodes["stream-output"].enabled,
        "messaging": nodes["publish"].dependencies["messaging"],
        "stream_sink": nodes["stream-output"].dependencies["stream_sink"],
    }


def test_callables_before_engines_exist() -> None:
    calls = _callables(_context())

    assert calls["infer"]() is True
    assert calls["stream"]() is True
    assert calls["stream-output"]() is True
    assert calls["messaging"]() is None
    assert calls["stream_sink"]() is None


def _publish_enabled(publish: bool):
    nodes = {n.node_id: n for n in node_definitions(_context(_config(publish=publish)), HealthSettings())}
    return nodes["publish"].enabled


def test_publish_enabled_follows_config() -> None:
    # EDGE_PUBLISH_ENABLED 啟動後不會改變，直接用 bool。
    assert _publish_enabled(False) is False
    assert _publish_enabled(True) is True


class _Client:
    def __init__(self, value) -> None:
        self.value = value
        self.routes: list[str] = []

    def is_connected(self, route_key: str):
        self.routes.append(route_key)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False), (None, None), (RuntimeError("x"), None)])
def test_messaging_dependency_uses_events_route(value, expected) -> None:
    context = _context()
    client = _Client(value)
    context.set_resource(MESSAGING_CLIENT_RESOURCE, client)

    assert _callables(context)["messaging"]() is expected
    assert client.routes == [EDGE_EVENTS_ROUTE]


class _ScheduledEngine:
    def __init__(self, phases: set[str]) -> None:
        self.phases = phases

    def has_work_for(self, phase: str) -> bool:
        return phase in self.phases


def _pipeline_context(*, streaming: bool = True) -> tuple[TaskContext, InferenceTask, StreamingTask]:
    context = _context()
    inference = InferenceTask(None)
    inference._engine = _ScheduledEngine({"working"})
    streaming_task = StreamingTask(None)
    streaming_task._enabled = streaming
    if streaming:
        engine = DefaultStreamingEngine(context, start_output_loop=False)
        engine._ffmpeg = FakeFfmpeg()
        engine._streaming_enabled_by_phase = {"working": True, "quiet": False}
        streaming_task._engine = engine
    context.set_resource("edge_pipeline", EdgePipeline([inference, streaming_task]))
    return context, inference, streaming_task


def test_infer_enabled_follows_schedule_of_current_phase() -> None:
    context, _, _ = _pipeline_context()
    calls = _callables(context)

    context.set_resource("edge_mode", "working")
    assert calls["infer"]() is True
    context.set_resource("edge_mode", "idle")
    assert calls["infer"]() is False


def test_stream_enabled_needs_streaming_and_phase() -> None:
    context, _, streaming_task = _pipeline_context()
    calls = _callables(context)

    context.set_resource("edge_mode", "working")
    assert calls["stream"]() is True and calls["stream-output"]() is True
    context.set_resource("edge_mode", "quiet")
    assert calls["stream"]() is False and calls["stream-output"]() is False

    disabled_context, _, _ = _pipeline_context(streaming=False)
    disabled = _callables(disabled_context)
    assert disabled["stream"]() is False and disabled["stream-output"]() is False
    assert disabled["stream_sink"]() is None


def test_stream_sink_uses_engine() -> None:
    context, _, streaming_task = _pipeline_context()
    calls = _callables(context)
    streaming_task._engine.sink_connected = lambda: False

    assert calls["stream_sink"]() is False


def test_callable_errors_do_not_propagate() -> None:
    context, inference, _ = _pipeline_context()
    calls = _callables(context)

    def boom(phase):
        raise RuntimeError("boom")

    inference._engine.has_work_for = boom
    assert calls["infer"]() is True
