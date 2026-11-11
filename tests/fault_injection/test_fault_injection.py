"""D1 用的故障注入類別：旗標檔存在時才製造故障，不存在時行為和父類別相同。"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

import numpy as np
import pytest
from smart_workflow import FatalTaskError, TaskContext

from edge.config import EdgeConfig
from edge.pipeline.tasks.inference.errors import raise_if_cuda_unrecoverable
from edge.pipeline.tasks.ingestion.sources import FrameSource, SourceFrame
from edge.schema import EdgeDetection

from fault_injection.inference import FaultInferenceEngine
from fault_injection.ingestion import FaultRtspIngestionEngine, StuckReadSource
from fault_injection.streaming import FaultStreamingEngine

FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


class EchoModel:
    def __init__(self, name, weights_path=None, label=None, **_):
        self.name = name
        self.warmups = 0

    def warmup(self, frame, metadata):
        self.warmups += 1

    def run(self, frame, metadata):
        return [EdgeDetection(track_id=1, class_name="car", bbox=[0, 0, 1, 1], bbox_confidence_score=1.0)]


@pytest.fixture
def faults(tmp_path, monkeypatch):
    monkeypatch.setenv("D1_FAULT_DIR", str(tmp_path))
    return tmp_path


def _context(monkeypatch, tmp_path) -> TaskContext:
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({"p": {"tasks": [{"name": "m", "mode": "every_frame",
                                                      "model_class": f"{__name__}:EchoModel"}]}}))
    monkeypatch.setenv("EDGE_SCHEDULE_PATH", str(schedule))
    monkeypatch.setenv("EDGE_RESOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("EDGE_CAMERA_ID", f"fi-{uuid.uuid4().hex[:6]}")
    monkeypatch.setenv("EDGE_STREAMING_ENABLED", "1")
    monkeypatch.setenv("EDGE_STREAMING_URL", "rtmp://127.0.0.1:1/none")
    return TaskContext(logger=logging.getLogger("test"), config=EdgeConfig(), monitor=None)


def _in_thread(fn):
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


# ---------------------------------------------------------------- inference


def test_inference_without_flags_behaves_like_scheduled_engine(faults, monkeypatch) -> None:
    engine = FaultInferenceEngine(_context(monkeypatch, faults))

    engine.warmup()
    outcome = engine.process(FRAME, phase="p")

    assert outcome.models_run == ["m"]
    assert len(outcome.detections) == 1


def test_startup_stuck_blocks_warmup_until_flag_removed(faults, monkeypatch) -> None:
    engine = FaultInferenceEngine(_context(monkeypatch, faults))
    (faults / "startup_stuck").touch()

    thread, box = _in_thread(engine.warmup)
    time.sleep(0.5)
    assert thread.is_alive()
    (faults / "startup_stuck").unlink()
    thread.join(2.0)

    assert not thread.is_alive() and "error" not in box


def test_infer_stuck_blocks_process(faults, monkeypatch) -> None:
    engine = FaultInferenceEngine(_context(monkeypatch, faults))
    (faults / "infer_stuck").touch()

    thread, box = _in_thread(lambda: engine.process(FRAME, phase="p"))
    time.sleep(0.5)
    assert thread.is_alive()
    (faults / "infer_stuck").unlink()
    thread.join(2.0)

    assert box["value"].models_run == ["m"]


def test_infer_error_raises_recoverable_error(faults, monkeypatch) -> None:
    engine = FaultInferenceEngine(_context(monkeypatch, faults))
    (faults / "infer_error").touch()

    with pytest.raises(RuntimeError) as excinfo:
        engine.process(FRAME, phase="p")
    raise_if_cuda_unrecoverable(excinfo.value)  # 不是 CUDA 錯誤，不會轉成 FatalTaskError


def test_infer_cuda_raises_illegal_memory_access(faults, monkeypatch) -> None:
    engine = FaultInferenceEngine(_context(monkeypatch, faults))
    (faults / "infer_cuda").touch()

    with pytest.raises(RuntimeError) as excinfo:
        engine.process(FRAME, phase="p")
    assert "illegal memory access" in str(excinfo.value)
    with pytest.raises(FatalTaskError):
        raise_if_cuda_unrecoverable(excinfo.value)


# ---------------------------------------------------------------- streaming


class _Ffmpeg:
    def __init__(self) -> None:
        self.writes = 0

    def write_frame(self, frame) -> None:
        self.writes += 1

    def is_alive(self) -> bool:
        return True

    def lifecycle_generation(self) -> int:
        return 0

    def close_async(self, *, reason: str) -> bool:
        return True

    def close(self) -> None:
        return None


def _streaming(monkeypatch, tmp_path) -> FaultStreamingEngine:
    engine = FaultStreamingEngine(_context(monkeypatch, tmp_path))
    engine._ffmpeg._inner = _Ffmpeg()
    return engine


def test_stream_write_stuck_blocks_inside_busy_scope(faults, monkeypatch) -> None:
    engine = _streaming(monkeypatch, faults)
    try:
        engine.push(FRAME, [], "p")
        (faults / "stream_write_stuck").touch()
        writes_before = engine._ffmpeg._inner.writes
        thread, _ = _in_thread(lambda: engine._write_output_frame(FRAME, "p"))
        time.sleep(0.5)

        assert thread.is_alive()
        assert engine.status().busy_since is not None
        (faults / "stream_write_stuck").unlink()
        thread.join(2.0)
        assert not thread.is_alive()
        assert engine._ffmpeg._inner.writes > writes_before
    finally:
        engine.close()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # 執行緒本來就該拋出
def test_stream_thread_crash_kills_output_thread(faults, monkeypatch) -> None:
    engine = _streaming(monkeypatch, faults)
    try:
        engine.push(FRAME, [], "p")
        time.sleep(0.2)
        assert engine.status().alive is True
        (faults / "stream_thread_crash").touch()
        deadline = time.monotonic() + 3.0
        while engine.status().alive and time.monotonic() < deadline:
            time.sleep(0.05)

        status = engine.status()
        assert status.alive is False
        assert status.active is True
    finally:
        engine.close()


def test_streaming_without_flags_keeps_output_thread_running(faults, monkeypatch) -> None:
    engine = _streaming(monkeypatch, faults)
    try:
        engine.push(FRAME, [], "p")
        time.sleep(0.5)
        assert engine.status().alive is True
        assert engine.status().success_count > 0
    finally:
        engine.close()


# ---------------------------------------------------------------- ingestion


class _Source(FrameSource):
    def __init__(self) -> None:
        super().__init__()
        self.aborted = False

    def open(self) -> None:
        return None

    def read(self):
        time.sleep(0.01)
        return SourceFrame(image=FRAME)

    def close(self) -> None:
        return None

    def abort(self) -> None:
        self.aborted = True


def test_stuck_read_source_ignores_abort_until_flag_removed(faults) -> None:
    inner = _Source()
    source = StuckReadSource(inner)
    assert source.read() is not None
    (faults / "ingest_read_stuck").touch()

    thread, box = _in_thread(source.read)
    time.sleep(0.3)
    source.abort()
    time.sleep(0.3)
    assert thread.is_alive()
    assert inner.aborted is True
    (faults / "ingest_read_stuck").unlink()
    thread.join(2.0)

    assert box["value"] is None


def test_stuck_read_source_delegates_state(faults) -> None:
    inner = _Source()
    inner.end_of_stream = True
    inner.last_error = "x"
    source = StuckReadSource(inner)

    assert source.end_of_stream is True
    assert source.last_error == "x"


def test_supervisor_crash_flag_makes_build_source_raise(faults, monkeypatch) -> None:
    monkeypatch.setenv("EDGE_INGEST_MODE", "rtsp")
    monkeypatch.setenv("EDGE_RTSP_URL", "rtsp://127.0.0.1:1/none")
    engine = FaultRtspIngestionEngine(_context(monkeypatch, faults))
    config = engine._cached_config

    assert isinstance(engine._build_source(config), StuckReadSource)
    (faults / "supervisor_crash").touch()
    with pytest.raises(RuntimeError, match="D1 fault"):
        engine._build_source(config)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # 執行緒本來就該拋出
def test_supervisor_crash_kills_supervisor_thread(faults, monkeypatch) -> None:
    monkeypatch.setenv("EDGE_INGEST_MODE", "rtsp")
    monkeypatch.setenv("EDGE_RTSP_URL", "rtsp://127.0.0.1:1/none")
    engine = FaultRtspIngestionEngine(_context(monkeypatch, faults))
    (faults / "supervisor_crash").touch()
    try:
        engine.start()
        deadline = time.monotonic() + 2.0
        while engine.status().alive and time.monotonic() < deadline:
            time.sleep(0.05)

        status = engine.status()
        assert status.alive is False
        assert status.active is True  # 不是 finished／fatal，所以會被判成 background_dead
    finally:
        engine.stop()

