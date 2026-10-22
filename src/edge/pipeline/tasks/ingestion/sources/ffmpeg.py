"""FFmpeg subprocess frame source: decode to rawvideo BGR on stdout."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from collections import deque
from collections.abc import Sequence
from functools import lru_cache

import numpy as np

from .base import FrameSource, FrameSourceError, SourceFrame, redact_url

LOGGER = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"ffmpeg version n?(\d+)\.(\d+)")
_STDERR_TAIL_LINES = 50
_TERMINATE_WAIT_SECONDS = 2.0


FfmpegVersion = tuple[int, int]


def parse_ffmpeg_version(banner: str) -> FfmpegVersion | None:
    match = _VERSION_RE.search(banner)
    return (int(match.group(1)), int(match.group(2))) if match else None


@lru_cache(maxsize=8)
def detect_ffmpeg_version(binary: str) -> FfmpegVersion | None:
    """回傳 ffmpeg (major, minor)；git build 等無法解析的版本字串回傳 None（視為新版）。"""
    try:
        result = subprocess.run([binary, "-hide_banner", "-version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FrameSourceError(f"無法執行 ffmpeg：{binary}（{exc}）") from exc
    return parse_ffmpeg_version(result.stdout)


def _is_rtsp(url: str) -> bool:
    return url.lower().startswith(("rtsp://", "rtsps://"))


def _at_least(version: FfmpegVersion | None, minimum: FfmpegVersion) -> bool:
    return version is None or version >= minimum


def _rtsp_input_args(url: str, transport: str, socket_timeout_seconds: float, version: FfmpegVersion | None) -> list[str]:
    if not _is_rtsp(url):
        return []
    # FFmpeg 5.0 起 RTSP 的 socket timeout 改名為 -timeout；4.x 的 -timeout 是 listen 模式的等待時間，不能混用。
    timeout_flag = "-timeout" if _at_least(version, (5, 0)) else "-stimeout"
    return ["-rtsp_transport", transport, timeout_flag, str(int(socket_timeout_seconds * 1_000_000))]


def build_ffmpeg_command(
    *,
    binary: str,
    url: str,
    width: int,
    height: int,
    version: FfmpegVersion | None,
    transport: str = "tcp",
    socket_timeout_seconds: float = 15.0,
    input_args: Sequence[str] = (),
) -> list[str]:
    # 解碼結果要和 OpenCV 逐位元相同：
    # - 不用 -fflags nobuffer（實測解出錯亂的畫面）與 -flags low_delay（改變 B-frame 處理）。
    # - passthrough：不依時間戳記補幀或丟幀（預設 CFR 會重複第一張）。
    # - in_color_matrix=bt601：OpenCV 一律以 BT.601 轉 BGR，模型一直看到的就是這個色彩。
    # - 一律縮放到固定尺寸：來源中途改解析度時，pipe 上的位元組仍對得上，不會錯位成破圖。
    passthrough = ["-fps_mode", "passthrough"] if _at_least(version, (5, 1)) else ["-vsync", "passthrough"]
    return [
        binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        *_rtsp_input_args(url, transport, socket_timeout_seconds, version),
        *input_args,
        "-i",
        url,
        "-an",
        "-sn",
        "-dn",
        *passthrough,
        "-vf",
        f"scale={width}:{height}:in_color_matrix=bt601",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def probe_frame_size(
    *,
    ffprobe: str,
    url: str,
    transport: str,
    timeout_seconds: float,
    version: FfmpegVersion | None,
) -> tuple[int, int]:
    cmd = [
        ffprobe,
        "-v",
        "error",
        *_rtsp_input_args(url, transport, timeout_seconds, version),
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        url,
    ]
    # 錯誤訊息不帶指令內容，且遮蔽 URL 帳密：訊息會進 log、health snapshot 與 monitor 事件。
    safe_url = redact_url(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        raise FrameSourceError(f"ffprobe 無法取得畫面尺寸：{safe_url}（逾時 {timeout_seconds:g} 秒）") from exc
    except OSError as exc:
        raise FrameSourceError(f"ffprobe 無法執行：{exc.strerror or exc}") from exc
    match = re.search(r"(\d+)x(\d+)", result.stdout)
    if result.returncode != 0 or match is None:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise FrameSourceError(f"ffprobe 無法取得畫面尺寸：{safe_url}（{redact_url(detail[0])}）")
    return int(match.group(1)), int(match.group(2))


class FfmpegFrameSource(FrameSource):
    """以 ffmpeg 子程序解碼；abort() 直接 kill 子程序，pipe 收到 EOF 後 read() 一定會返回。"""

    def __init__(
        self,
        url: str,
        *,
        binary: str = "ffmpeg",
        transport: str = "tcp",
        open_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 15.0,
        width: int | None = None,
        height: int | None = None,
        input_args: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self._url = url
        self._binary = binary
        self._transport = transport
        self._open_timeout_seconds = open_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._size = (width, height) if width and height else None
        self._input_args = list(input_args)
        self._process: subprocess.Popen | None = None
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._stderr_thread: threading.Thread | None = None
        self._shape: tuple[int, int, int] | None = None

    def open(self) -> None:
        version = detect_ffmpeg_version(self._binary)
        width, height = self._size or probe_frame_size(
            ffprobe=self._ffprobe_binary(),
            url=self._url,
            transport=self._transport,
            timeout_seconds=self._open_timeout_seconds,
            version=version,
        )
        cmd = build_ffmpeg_command(
            binary=self._binary,
            url=self._url,
            width=width,
            height=height,
            version=version,
            transport=self._transport,
            socket_timeout_seconds=self._read_timeout_seconds,
            input_args=self._input_args,
        )
        try:
            self._process = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )
        except OSError as exc:
            raise FrameSourceError(f"無法啟動 ffmpeg：{exc}") from exc
        self._shape = (height, width, 3)
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(self._process,), name="FfmpegIngestionStderr", daemon=True
        )
        self._stderr_thread.start()
        LOGGER.info("ffmpeg ingestion started pid=%s size=%dx%d", self._process.pid, width, height)

    def read(self) -> SourceFrame | None:
        process, shape = self._process, self._shape
        if process is None or process.stdout is None or shape is None:
            return None
        image = np.empty(shape, dtype=np.uint8)
        view = memoryview(image).cast("B")
        filled = 0
        while filled < len(view):
            count = process.stdout.readinto(view[filled:])
            if not count:
                self._mark_stream_end(process)
                return None
            filled += count
        return SourceFrame(image=image)

    def abort(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)

    def _mark_stream_end(self, process: subprocess.Popen) -> None:
        try:
            returncode = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            returncode = None
        # ffmpeg 正常結束（rc=0）代表輸入讀完；其他情況（被 kill、連線中斷）視為讀取失敗。
        self.end_of_stream = returncode == 0
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.5)
        tail = self._stderr_tail[-1] if self._stderr_tail else None
        self.last_error = f"ffmpeg 結束（rc={returncode}）" + (f"：{tail}" if tail else "")

    def _drain_stderr(self, process: subprocess.Popen) -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr_tail.append(redact_url(line))
        except (OSError, ValueError):
            pass

    def _ffprobe_binary(self) -> str:
        directory = os.path.dirname(self._binary)
        return os.path.join(directory, "ffprobe") if directory else "ffprobe"
