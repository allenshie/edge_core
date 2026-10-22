from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from smart_workflow import TaskError

from edge.config import IngestionConfig, IngestionWorkerConfig, RtspConfig
from edge.pipeline.tasks.ingestion.engines import RtspIngestionEngine
from edge.pipeline.tasks.ingestion.sources import FfmpegFrameSource, OpenCvFileSource, OpenCvFrameSource
from edge.pipeline.tasks.ingestion.sources import ffmpeg as ffmpeg_module
from edge.pipeline.tasks.ingestion.sources.ffmpeg import build_ffmpeg_command, parse_ffmpeg_version

VIDEO = Path(__file__).resolve().parents[3] / "test_data" / "videos" / "TL-cam1-eastbound-1.mp4"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

FAKE_FFMPEG = """#!{python}
import sys, time
if "-version" in sys.argv:
    print("ffmpeg version 5.1.6-0+deb12u1 Copyright (c) 2000-2024 the FFmpeg developers")
    sys.exit(0)
mode, frames, size = {mode!r}, {frames}, {size}
out = sys.stdout.buffer
for i in range(frames):
    out.write(bytes([i % 256]) * size)
    out.flush()
if mode == "partial":
    out.write(b"x" * (size // 2))
    out.flush()
    sys.stderr.write("broken pipe from source\\n")
    sys.exit(1)
if mode == "hang":
    time.sleep(3600)
sys.exit(0)
"""


@pytest.fixture(autouse=True)
def _clear_version_cache():
    ffmpeg_module.detect_ffmpeg_version.cache_clear()
    yield
    ffmpeg_module.detect_ffmpeg_version.cache_clear()


def _fake_ffmpeg(tmp_path: Path, *, mode: str, frames: int, width: int = 4, height: int = 2) -> str:
    script = tmp_path / f"fake_ffmpeg_{mode}_{frames}"
    script.write_text(FAKE_FFMPEG.format(python=sys.executable, mode=mode, frames=frames, size=width * height * 3))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.mark.parametrize(
    ("banner", "expected"),
    [
        ("ffmpeg version 4.2.7-0ubuntu0.1 Copyright (c) 2000-2022", (4, 2)),
        ("ffmpeg version 5.1.6-0+deb12u1 Copyright (c) 2000-2024", (5, 1)),
        ("ffmpeg version n6.1.1 Copyright (c) 2000-2023", (6, 1)),
        ("ffmpeg version N-112233-gabcdef Copyright", None),
        ("", None),
    ],
)
def test_parse_ffmpeg_version(banner: str, expected: tuple[int, int] | None) -> None:
    assert parse_ffmpeg_version(banner) == expected


def test_rtsp_command_uses_timeout_on_ffmpeg5() -> None:
    cmd = build_ffmpeg_command(
        binary="ffmpeg",
        url="rtsp://cam/stream",
        width=1280,
        height=720,
        version=(5, 1),
        transport="tcp",
        socket_timeout_seconds=15,
        input_args=["-hwaccel", "cuda"],
    )

    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert cmd[cmd.index("-timeout") + 1] == "15000000"
    assert "-stimeout" not in cmd
    assert cmd.index("-hwaccel") < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == "rtsp://cam/stream"
    assert cmd[cmd.index("-vf") + 1] == "scale=1280:720:in_color_matrix=bt601"
    assert cmd[cmd.index("-fps_mode") + 1] == "passthrough"
    assert "-vsync" not in cmd
    # nobuffer 會讓解出的畫面錯亂、low_delay 會改變 B-frame 處理，兩者都不使用。
    assert "nobuffer" not in cmd and "low_delay" not in cmd
    assert cmd[-5:] == ["-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]


def test_rtsp_command_uses_stimeout_on_ffmpeg4() -> None:
    cmd = build_ffmpeg_command(
        binary="ffmpeg",
        url="rtsp://cam/stream",
        width=640,
        height=480,
        version=(4, 2),
        transport="udp",
        socket_timeout_seconds=2.5,
    )

    assert cmd[cmd.index("-stimeout") + 1] == "2500000"
    assert "-timeout" not in cmd
    assert cmd[cmd.index("-rtsp_transport") + 1] == "udp"
    assert cmd[cmd.index("-vsync") + 1] == "passthrough"
    assert "-fps_mode" not in cmd


def test_unknown_version_is_treated_as_new() -> None:
    cmd = build_ffmpeg_command(binary="ffmpeg", url="rtsp://cam/stream", width=4, height=2, version=None)

    assert "-timeout" in cmd and "-fps_mode" in cmd


def test_non_rtsp_command_skips_rtsp_options() -> None:
    cmd = build_ffmpeg_command(
        binary="ffmpeg",
        url="/data/video.mp4",
        width=640,
        height=480,
        version=(5, 1),
        transport="tcp",
        socket_timeout_seconds=15,
    )

    assert "-rtsp_transport" not in cmd
    assert "-timeout" not in cmd
    assert "-vf" in cmd


def test_source_reads_frames_then_end_of_stream(tmp_path) -> None:
    source = FfmpegFrameSource("rtsp://cam/stream", binary=_fake_ffmpeg(tmp_path, mode="ok", frames=3), width=4, height=2)
    source.open()
    try:
        frames = [source.read() for _ in range(3)]
        assert [int(frame.image[0, 0, 0]) for frame in frames] == [0, 1, 2]
        assert frames[0].image.shape == (2, 4, 3)
        assert frames[0].image.dtype == np.uint8
        assert source.read() is None
        assert source.end_of_stream is True
    finally:
        source.close()


def test_partial_frame_is_not_returned(tmp_path) -> None:
    source = FfmpegFrameSource(
        "rtsp://cam/stream", binary=_fake_ffmpeg(tmp_path, mode="partial", frames=1), width=4, height=2
    )
    source.open()
    try:
        assert source.read() is not None
        assert source.read() is None
        assert source.end_of_stream is False
        assert "broken pipe from source" in (source.last_error or "")
    finally:
        source.close()


def test_abort_unblocks_read_and_close_reaps_process(tmp_path) -> None:
    source = FfmpegFrameSource("rtsp://cam/stream", binary=_fake_ffmpeg(tmp_path, mode="hang", frames=0), width=4, height=2)
    source.open()
    result: list[object] = []
    reader = threading.Thread(target=lambda: result.append(source.read()))
    reader.start()
    time.sleep(0.2)
    assert reader.is_alive()

    started = time.monotonic()
    source.abort()
    reader.join(timeout=1.0)

    assert not reader.is_alive()
    assert time.monotonic() - started < 1.0
    assert result == [None]
    process = source._process
    source.close()
    assert process is not None and process.returncode is not None


def test_engine_selects_source_by_decoder(tmp_path) -> None:
    def engine(decoder: str) -> RtspIngestionEngine:
        rtsp = RtspConfig(url="rtsp://cam/stream", decoder=decoder, ffmpeg_bin=_fake_ffmpeg(tmp_path, mode="ok", frames=1))
        context = SimpleNamespace(config=SimpleNamespace(ingestion=IngestionConfig(mode="rtsp", rtsp=rtsp)))
        return RtspIngestionEngine(context)

    assert isinstance(engine("ffmpeg")._build_source(engine("ffmpeg")._cached_config), FfmpegFrameSource)
    assert isinstance(engine("opencv")._build_source(engine("opencv")._cached_config), OpenCvFrameSource)
    with pytest.raises(TaskError, match="EDGE_RTSP_DECODER"):
        bad = engine("gstreamer")
        bad._validate_config(bad._cached_config)


def test_missing_ffmpeg_binary_fails_fast() -> None:
    rtsp = RtspConfig(url="rtsp://cam/stream", decoder="ffmpeg", ffmpeg_bin="/nonexistent/ffmpeg")
    context = SimpleNamespace(config=SimpleNamespace(ingestion=IngestionConfig(mode="rtsp", rtsp=rtsp)))
    engine = RtspIngestionEngine(context)

    with pytest.raises(TaskError, match="找不到 ffmpeg"):
        engine.start()


def test_supervisor_replaces_hung_ffmpeg_without_stuck_workers(tmp_path) -> None:
    hung = _fake_ffmpeg(tmp_path, mode="hang", frames=2)
    healthy = _fake_ffmpeg(tmp_path, mode="ok", frames=100000)
    config = IngestionWorkerConfig(
        open_timeout_seconds=0.5,
        read_timeout_seconds=0.5,
        stall_seconds=0.2,
        reconnect_max_seconds=0.05,
        worker_stop_timeout_seconds=1.0,
        max_stuck_workers=1,
    )
    rtsp = RtspConfig(url="rtsp://cam/stream", decoder="ffmpeg", frame_width=4, frame_height=2, reconnect_seconds=0.01)
    context = SimpleNamespace(config=SimpleNamespace(ingestion=IngestionConfig(mode="rtsp", rtsp=rtsp, worker=config)))
    engine = RtspIngestionEngine(context)
    binaries = iter([hung, healthy])
    created: list[FfmpegFrameSource] = []
    original = engine._build_source

    def build(cfg):
        cfg.ffmpeg_bin = next(binaries, healthy)
        source = original(cfg)
        created.append(source)
        return source

    engine._build_source = build
    try:
        engine.fetch()
        deadline = time.monotonic() + 5
        while engine.health_snapshot()["worker_generation"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = engine.health_snapshot()
        assert snapshot["worker_generation"] >= 2
        assert snapshot["stuck_workers"] == 0
        assert snapshot["fatal"] is False
        assert created[0]._process is None  # close() 已回收並清掉子程序
    finally:
        engine.stop()


@pytest.mark.skipif(not (HAS_FFMPEG and VIDEO.exists()), reason="ffmpeg or test video not available")
def test_real_ffmpeg_frames_are_identical_to_opencv() -> None:
    # 色彩轉換與幀序都要和 OpenCV 一致，模型看到的畫面才不會因為換解碼方式而改變。
    ffmpeg_source = FfmpegFrameSource(str(VIDEO))
    opencv_source = OpenCvFileSource(VIDEO, loop=False)
    ffmpeg_source.open()
    opencv_source.open()
    try:
        for index in range(60):
            a = ffmpeg_source.read()
            b = opencv_source.read()
            assert a is not None and b is not None
            assert a.image.shape == b.image.shape
            assert np.array_equal(a.image, b.image), f"frame {index} differs"
    finally:
        ffmpeg_source.close()
        opencv_source.close()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not available")
def test_real_ffmpeg_version_is_detected() -> None:
    banner = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False).stdout
    assert ffmpeg_module.detect_ffmpeg_version("ffmpeg") == parse_ffmpeg_version(banner)


def test_redact_url_hides_credentials() -> None:
    from edge.pipeline.tasks.ingestion.sources import redact_url

    assert redact_url("rtsp://admin:s3cret@10.0.0.5:554/live") == "rtsp://***@10.0.0.5:554/live"
    assert redact_url("open rtsp://u:p@h/a and rtsps://x@h/b") == "open rtsp://***@h/a and rtsps://***@h/b"
    assert redact_url("rtsp://10.0.0.5/live") == "rtsp://10.0.0.5/live"


def test_ffprobe_timeout_message_has_no_command_or_password(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(ffmpeg_module.subprocess, "run", fake_run)

    with pytest.raises(ffmpeg_module.FrameSourceError) as excinfo:
        ffmpeg_module.probe_frame_size(
            ffprobe="ffprobe", url="rtsp://admin:s3cret@cam/live", transport="tcp", timeout_seconds=10, version=(5, 1)
        )

    message = str(excinfo.value)
    assert "s3cret" not in message
    assert "-select_streams" not in message
    assert "逾時 10 秒" in message


def test_ffmpeg_stderr_tail_is_redacted(tmp_path) -> None:
    script = tmp_path / "fake_ffmpeg_leak"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if '-version' in sys.argv:\n"
        "    print('ffmpeg version 5.1.6'); sys.exit(0)\n"
        "sys.stderr.write('rtsp://admin:s3cret@cam/live: Connection refused\\n'); sys.exit(1)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    source = FfmpegFrameSource("rtsp://admin:s3cret@cam/live", binary=str(script), width=4, height=2)
    source.open()
    try:
        assert source.read() is None
        assert "s3cret" not in (source.last_error or "")
        assert "Connection refused" in (source.last_error or "")
    finally:
        source.close()
