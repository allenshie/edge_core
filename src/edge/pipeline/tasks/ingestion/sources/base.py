"""Frame source contract shared by ingestion workers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class FrameSourceError(Exception):
    """Raised when a frame source cannot be opened."""


@dataclass(frozen=True)
class SourceFrame:
    image: Any
    # 預留給來源時間戳記（例如 RTSP RTCP SR / pts 換算）；目前的 source 一律為 None。
    source_ts: datetime | None = None


class FrameSource(ABC):
    """一次連線的影像來源。

    open / read / close 只能由所屬 worker 的 thread 呼叫；abort 可以從其他 thread 呼叫，
    用來解開卡住的 read。
    """

    def __init__(self) -> None:
        self.end_of_stream = False
        self.last_error: str | None = None

    @abstractmethod
    def open(self) -> None:
        """開啟來源，失敗時拋出 FrameSourceError。"""

    @abstractmethod
    def read(self) -> SourceFrame | None:
        """讀取下一張畫面；讀取失敗或 EOF 時回傳 None（EOF 時 end_of_stream 為 True）。"""

    @abstractmethod
    def close(self) -> None:
        """釋放來源。"""

    def abort(self) -> None:
        """跨 thread 中斷卡住的 read；無法中斷的來源維持 no-op。"""
