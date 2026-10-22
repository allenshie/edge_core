from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from edge.config import IngestionConfig, IngestionWorkerConfig
from edge.pipeline.tasks.ingestion.sources import FrameSourceError, OpenCvFileSource, OpenCvFrameSource

VIDEO = Path(__file__).resolve().parents[3] / "test_data" / "videos" / "TL-cam1-eastbound-1.mp4"


class _FakeCapture:
    instances: list[_FakeCapture] = []

    def __init__(self, *args) -> None:
        self.args = args
        self.props: dict[int, float] = {}
        self.released = False
        self.opened = True
        _FakeCapture.instances.append(self)

    def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2 API
        return self.opened

    def set(self, prop: int, value: float) -> bool:
        self.props[prop] = value
        return True

    def read(self):
        return False, None

    def release(self) -> None:
        self.released = True


@pytest.fixture
def fake_capture(monkeypatch):
    _FakeCapture.instances = []
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    return _FakeCapture


def test_worker_config_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_INGESTION_OPEN_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("EDGE_INGESTION_READ_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("EDGE_INGESTION_STALL_SECONDS", "4")
    monkeypatch.setenv("EDGE_INGESTION_RECONNECT_MAX_SECONDS", "8")
    monkeypatch.setenv("EDGE_INGESTION_WORKER_STOP_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("EDGE_INGESTION_MAX_STUCK_WORKERS", "5")

    cfg = IngestionConfig().worker

    assert cfg == IngestionWorkerConfig(
        open_timeout_seconds=3.0,
        read_timeout_seconds=2.0,
        stall_seconds=4.0,
        reconnect_max_seconds=8.0,
        worker_stop_timeout_seconds=1.5,
        max_stuck_workers=5,
    )


def test_worker_config_defaults() -> None:
    cfg = IngestionWorkerConfig()

    assert cfg.open_timeout_seconds == 10.0
    assert cfg.read_timeout_seconds == 5.0
    assert cfg.stall_seconds == 10.0
    assert cfg.reconnect_max_seconds == 30.0
    assert cfg.worker_stop_timeout_seconds == 2.0
    assert cfg.max_stuck_workers == 3


def test_url_source_opens_with_ffmpeg_backend_and_timeouts(fake_capture) -> None:
    source = OpenCvFrameSource(
        "rtsp://example/stream",
        open_timeout_seconds=10,
        read_timeout_seconds=5,
        properties={cv2.CAP_PROP_FRAME_WIDTH: 640},
    )

    source.open()

    capture = fake_capture.instances[0]
    assert capture.args == (
        "rtsp://example/stream",
        cv2.CAP_FFMPEG,
        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
    )
    assert capture.props == {cv2.CAP_PROP_FRAME_WIDTH: 640}


def test_device_source_opens_without_timeouts(fake_capture) -> None:
    source = OpenCvFrameSource(0, properties={cv2.CAP_PROP_FPS: 15})

    source.open()

    assert fake_capture.instances[0].args == (0,)
    assert fake_capture.instances[0].props == {cv2.CAP_PROP_FPS: 15}


def test_open_failure_releases_and_raises(fake_capture, monkeypatch) -> None:
    monkeypatch.setattr(_FakeCapture, "isOpened", lambda self: False)
    source = OpenCvFrameSource("rtsp://example/stream", open_timeout_seconds=1, read_timeout_seconds=1)

    with pytest.raises(FrameSourceError):
        source.open()

    assert fake_capture.instances[0].released is True


def test_read_failure_is_not_end_of_stream(fake_capture) -> None:
    source = OpenCvFrameSource("rtsp://example/stream", open_timeout_seconds=1, read_timeout_seconds=1)
    source.open()

    assert source.read() is None
    assert source.end_of_stream is False

    source.close()
    assert fake_capture.instances[0].released is True


@pytest.mark.skipif(not VIDEO.exists(), reason="test video not available")
def test_file_source_reads_frames_and_reports_eof() -> None:
    source = OpenCvFileSource(VIDEO, loop=False)
    source.open()
    try:
        frame = source.read()
        assert frame is not None
        assert frame.image.ndim == 3
        assert frame.source_ts is None

        source._capture.set(cv2.CAP_PROP_POS_FRAMES, source._capture.get(cv2.CAP_PROP_FRAME_COUNT))
        assert source.read() is None
        assert source.end_of_stream is True
        assert source.last_error == "影片播放結束，且未啟用 EDGE_FILE_LOOP"
    finally:
        source.close()


@pytest.mark.skipif(not VIDEO.exists(), reason="test video not available")
def test_file_source_rewinds_when_looping() -> None:
    source = OpenCvFileSource(VIDEO, loop=True)
    source.open()
    try:
        source._capture.set(cv2.CAP_PROP_POS_FRAMES, source._capture.get(cv2.CAP_PROP_FRAME_COUNT))
        frame = source.read()
        assert frame is not None
        assert source.end_of_stream is False
    finally:
        source.close()


def test_missing_file_raises_open_error(tmp_path) -> None:
    source = OpenCvFileSource(tmp_path / "missing.mp4", loop=True)

    with pytest.raises(FrameSourceError, match="無法開啟指定影片"):
        source.open()
