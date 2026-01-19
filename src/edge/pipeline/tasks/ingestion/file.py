"""File-based ingestion task for local MP4 sim."""
from __future__ import annotations

from .base import BaseIngestionTask
from .engine import FileIngestionEngine


class FileIngestionTask(BaseIngestionTask):
    name = "edge-file-ingestion"
    engine_cls = FileIngestionEngine
