"""spec-edge.md 第 5 節第 7 步：InitPipelineTask 綁定背景執行緒並暖機；停用健康回報時都不做。"""
from __future__ import annotations

import logging

import pytest
from smart_workflow import HealthRuntime, TaskContext

from edge.config import EdgeConfig
from edge.messaging import MESSAGING_CLIENT_RESOURCE
from edge.pipeline.pipeline import InitPipelineTask
from edge.pipeline.tasks.inference.engine import BaseInferenceEngine, InferenceOutcome
from edge.pipeline.tasks.ingestion.engines import BaseIngestionEngine

from edge_health_testkit import Monitor

WARMUPS: list[str] = []


class WarmEngine(BaseInferenceEngine):
    def process(self, frame, *, phase=None, metadata=None):
        return InferenceOutcome(detections=[])

    def warmup(self) -> None:
        WARMUPS.append("warm")


class _Client:
    def publish(self, route_key, payload) -> bool:
        return True

    def is_connected(self, route_key):
        return True


class _Recorder:
    def __getattr__(self, name):
        return lambda *args: None


class _FakeMonitor:
    def recorder(self, node_id):
        return _Recorder()


@pytest.fixture
def env(monkeypatch):
    WARMUPS.clear()
    monkeypatch.setenv("EDGE_INGEST_MODE", "file")
    monkeypatch.setenv("EDGE_FILE_PATH", "/nonexistent.mp4")
    monkeypatch.setenv("INFERENCE_ENGINE_CLASS", f"{__name__}:WarmEngine")
    monkeypatch.setenv("EDGE_STREAMING_ENABLED", "1")
    monkeypatch.setenv("EDGE_STREAMING_URL", "rtmp://127.0.0.1:1/none")
    monkeypatch.setenv("EDGE_INGESTION_OPEN_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("EDGE_INGESTION_STALL_SECONDS", "20")
    monkeypatch.setenv("EDGE_INGESTION_WORKER_STOP_TIMEOUT_SECONDS", "2")
    for name in ("PUBLISH_ENGINE_CLASS", "STREAMING_ENGINE_CLASS", "INGESTION_ENGINE_CLASS"):
        monkeypatch.delenv(name, raising=False)


def _run(health: HealthRuntime | None) -> tuple[TaskContext, list[tuple]]:
    context = TaskContext(logger=logging.getLogger("test"), config=EdgeConfig(), monitor=Monitor(), health=health)
    context.set_resource(MESSAGING_CLIENT_RESOURCE, _Client())
    binds: list[tuple] = []
    original = context.health.bind_background

    def spy(node_id, status, **kwargs):
        binds.append((node_id, status, kwargs))
        original(node_id, status, **kwargs)

    context.health.bind_background = spy
    InitPipelineTask().execute(context)
    return context, binds


def _close(context: TaskContext) -> None:
    pipeline = context.get_resource("edge_pipeline")
    if pipeline is not None:
        pipeline.close(context)


def test_health_enabled_binds_background_and_warms_up(env) -> None:
    health = HealthRuntime(_FakeMonitor())
    context, binds = _run(health)
    try:
        assert [b[0] for b in binds] == ["ingest-worker", "stream-output"]
        ingest_status = binds[0][1]()
        assert isinstance(binds[0][1].__self__, BaseIngestionEngine)
        assert ingest_status.active is False
        assert binds[0][2] == {"stuck_exit_s": 37.0}
        assert binds[1][2] == {}
        assert health.background_nodes() == ["ingest-worker", "stream-output"]
        assert WARMUPS == ["warm"]
    finally:
        _close(context)


def test_streaming_disabled_binds_only_ingestion(env, monkeypatch) -> None:
    monkeypatch.setenv("EDGE_STREAMING_ENABLED", "0")
    context, binds = _run(HealthRuntime(_FakeMonitor()))
    try:
        assert [b[0] for b in binds] == ["ingest-worker"]
    finally:
        _close(context)


def test_health_disabled_neither_binds_nor_warms_up(env) -> None:
    context, binds = _run(None)
    try:
        assert context.health.enabled is False
        assert binds == []
        assert WARMUPS == []
    finally:
        _close(context)


def test_stuck_exit_never_below_30_seconds(env, monkeypatch) -> None:
    monkeypatch.setenv("EDGE_INGESTION_OPEN_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("EDGE_INGESTION_STALL_SECONDS", "2")
    context, binds = _run(HealthRuntime(_FakeMonitor()))
    try:
        assert binds[0][2] == {"stuck_exit_s": 30.0}
    finally:
        _close(context)
