from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from smart_workflow import ProbeConfig, TaskContext, TaskError
from smart_workflow.health_server import _evaluate_liveness, _evaluate_readiness

from edge.config import FileSourceConfig, IngestionConfig, IngestionWorkerConfig
from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.ingestion.engines import FileIngestionEngine
from edge.runtime.health_runtime import EdgeHealthState

VIDEO = Path(__file__).resolve().parents[3] / "test_data" / "videos" / "TL-cam1-eastbound-1.mp4"


class _Monitor:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def report_event(self, event_type: str, detail: str | None = None, **extra) -> None:
        self.events.append((event_type, {"detail": detail, **extra}))

    def heartbeat(self, **kwargs) -> None:
        return None


def _context(file_cfg: FileSourceConfig) -> TaskContext:
    config = SimpleNamespace(
        ingestion=IngestionConfig(mode="file", file=file_cfg, worker=IngestionWorkerConfig()),
        health_report_interval_seconds=0,
    )
    return TaskContext(logger=logging.getLogger("test"), config=config, monitor=_Monitor())


class _FatalEngine:
    source_label = "rtsp"

    def __init__(self) -> None:
        self.restart_calls = 0
        self.capture_rate_meter = None

    def fetch(self):
        raise TaskError("rtsp ingestion fatal：3 個 capture worker 無法停止")

    def health_snapshot(self) -> dict:
        return {"worker_alive": False, "fatal": True, "fatal_reason": "3 個 capture worker 無法停止"}

    def is_started(self) -> bool:
        return False

    def restart(self) -> None:
        self.restart_calls += 1


def test_task_has_no_restart_path() -> None:
    assert not hasattr(IngestionTask, "_maybe_restart_ingestion")
    assert not hasattr(FileIngestionEngine, "restart")


def test_fatal_engine_marks_health_state_once() -> None:
    context = _context(FileSourceConfig(path=str(VIDEO)))
    health_state = EdgeHealthState(logging.getLogger("test"))
    context.set_resource("health_state", health_state)
    task = IngestionTask()
    task._mode = "rtsp"
    engine = _FatalEngine()
    task._engine = engine

    for _ in range(2):
        with pytest.raises(TaskError):
            task.run(context)

    assert health_state.fatal_reason == "rtsp ingestion fatal: 3 個 capture worker 無法停止"
    assert [event for event, _ in context.monitor.events].count("error") == 1
    assert engine.restart_calls == 0


def test_fatal_engine_without_health_server_only_logs(caplog) -> None:
    context = _context(FileSourceConfig(path=str(VIDEO)))
    task = IngestionTask()
    task._mode = "rtsp"
    task._engine = _FatalEngine()

    with caplog.at_level(logging.CRITICAL), pytest.raises(TaskError):
        task.run(context)

    assert "rtsp ingestion fatal" in caplog.text


def test_fatal_health_state_stops_loop_and_progress() -> None:
    probe = ProbeConfig(liveness_timeout_seconds=30, readiness_timeout_seconds=30, startup_grace_seconds=10)
    health_state = EdgeHealthState(logging.getLogger("test"))
    health_state.mark_startup_ok()
    health_state.mark_loop_tick()
    health_state.mark_progress()

    health_state.mark_fatal("rtsp ingestion fatal")
    health_state.mark_loop_tick()
    health_state.mark_progress()

    snapshot = health_state.snapshot()
    later = time.time() + 31
    assert _evaluate_liveness(snapshot, later, probe)[0] is False
    assert _evaluate_readiness(snapshot, later, probe)[0] is False
    assert snapshot.last_error == "rtsp ingestion fatal"
    assert _evaluate_liveness(snapshot, time.time(), probe)[0] is True


@pytest.mark.skipif(not VIDEO.exists(), reason="test video not available")
def test_file_ingestion_end_to_end() -> None:
    context = _context(FileSourceConfig(path=str(VIDEO), loop=True, drop_frames=0, fps=None))
    task = IngestionTask(context)
    try:
        seqs = []
        for _ in range(5):
            result = task.run(context)
            seqs.append(result.payload["frame_meta"]["frame_seq"])
            time.sleep(0.02)
        assert context.get_resource("decoded_frame") is not None
        assert seqs == sorted(seqs) and seqs[-1] > seqs[0]
        assert task.snapshot_health(context)["alive"] is True
    finally:
        records = task.close(context)
    assert records[0]["state"] == "done"


def test_missing_file_path_is_reported() -> None:
    context = _context(FileSourceConfig(path=None))
    task = IngestionTask(context)

    with pytest.raises(TaskError, match="EDGE_FILE_PATH 尚未設定"):
        task.run(context)


def test_unopenable_file_finishes_with_original_message(tmp_path) -> None:
    context = _context(FileSourceConfig(path=str(tmp_path / "missing.mp4")))
    task = IngestionTask(context)
    try:
        with pytest.raises(TaskError, match="無法開啟指定影片"):
            task.run(context)
        assert task._engine.health_snapshot()["worker_generation"] == 1
    finally:
        task.close(context)
