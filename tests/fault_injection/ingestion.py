"""取流 Engine 的故障：ingest_read_stuck（read() 不返回且不理會 abort）、supervisor_crash（_build_source 拋出）。"""
from __future__ import annotations

from typing import Any

from edge.pipeline.tasks.ingestion.engines.rtsp import RtspIngestionEngine
from edge.pipeline.tasks.ingestion.sources import FrameSource, SourceFrame

from .flags import block_while, is_set


class StuckReadSource(FrameSource):
    """包住真正的來源：旗標存在時 read() 一直阻塞，abort 只轉交給內部來源，不會解開阻塞（情境 11）。"""

    def __init__(self, inner: FrameSource) -> None:
        super().__init__()
        self._inner = inner

    @property  # type: ignore[override]
    def end_of_stream(self) -> bool:
        return getattr(self._inner, "end_of_stream", False)

    @end_of_stream.setter
    def end_of_stream(self, value: bool) -> None:
        if hasattr(self, "_inner"):
            self._inner.end_of_stream = value

    @property  # type: ignore[override]
    def last_error(self) -> str | None:
        return getattr(self._inner, "last_error", None)

    @last_error.setter
    def last_error(self, value: str | None) -> None:
        if hasattr(self, "_inner"):
            self._inner.last_error = value

    def open(self) -> None:
        self._inner.open()

    def read(self) -> SourceFrame | None:
        if is_set("ingest_read_stuck"):
            block_while("ingest_read_stuck")
            return None
        return self._inner.read()

    def close(self) -> None:
        self._inner.close()

    def abort(self) -> None:
        self._inner.abort()


class FaultRtspIngestionEngine(RtspIngestionEngine):
    def _build_source(self, config: Any) -> FrameSource:
        if is_set("supervisor_crash"):
            # 在 supervisor 執行緒內拋出非預期的例外，supervisor 會結束。
            raise RuntimeError("D1 fault: _build_source failed")
        return StuckReadSource(super()._build_source(config))


__all__ = ["FaultRtspIngestionEngine", "StuckReadSource"]
