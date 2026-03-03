"""Edge pipeline tasks package."""

from .ingestion import FileIngestionTask, RtspIngestionTask
from .inference import InferenceTask
from .publish import PublishResultTask
from .streaming import StreamingTask

__all__ = [
    "FileIngestionTask",
    "InferenceTask",
    "PublishResultTask",
    "RtspIngestionTask",
    "StreamingTask",
]
