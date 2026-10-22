"""Frame sources used by ingestion workers."""

from .base import FrameSource, FrameSourceError, SourceFrame
from .opencv import OpenCvFileSource, OpenCvFrameSource

__all__ = [
    "FrameSource",
    "FrameSourceError",
    "SourceFrame",
    "OpenCvFileSource",
    "OpenCvFrameSource",
]
