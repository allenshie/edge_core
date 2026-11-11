"""推流 Engine 的故障：stream_write_stuck（寫入阻塞）、stream_thread_crash（輸出執行緒拋出未處理的例外）。"""
from __future__ import annotations

from typing import Any

from edge.pipeline.tasks.streaming.engines.shm import ShmStreamingEngine

from .flags import block_while, is_set


class _BlockingWriter:
    """包住 FfmpegProcessManager：旗標存在時 write_frame 阻塞，其他呼叫原樣轉交。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def write_frame(self, frame: Any) -> None:
        block_while("stream_write_stuck")  # 情境 10
        self._inner.write_frame(frame)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class FaultStreamingEngine(ShmStreamingEngine):
    def __init__(self, context: Any = None) -> None:
        super().__init__(context)
        self._ffmpeg = _BlockingWriter(self._ffmpeg)

    def _advance_output_deadline(self, now: float) -> None:
        # 在 _output_loop 的 try 之外拋出，輸出執行緒會結束（情境 12）。
        if is_set("stream_thread_crash"):
            raise RuntimeError("D1 fault: streaming output thread crash")
        super()._advance_output_deadline(now)


__all__ = ["FaultStreamingEngine"]
