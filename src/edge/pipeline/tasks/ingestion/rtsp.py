"""RTSP ingestion task implementation."""
from __future__ import annotations

from .base import BaseIngestionTask
from .engine import RtspIngestionEngine


class RtspIngestionTask(BaseIngestionTask):
    name = "edge-rtsp-ingestion"
    engine_cls = RtspIngestionEngine
