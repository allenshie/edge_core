from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from smart_workflow import TaskContext, TaskError

from edge.config import FileSourceConfig, IngestionConfig, IngestionWorkerConfig
from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.ingestion.engines import FileIngestionEngine

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


def test_task_has_no_restart_path() -> None:
    assert not hasattr(IngestionTask, "_maybe_restart_ingestion")
    assert not hasattr(FileIngestionEngine, "restart")


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
