"""Edge pipeline tasks package."""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "IngestionTask",
    "InferenceTask",
    "MatchingResultTask",
    "PublishResultTask",
    "StreamingTask",
]

_EXPORTS = {
    "IngestionTask": ".ingestion",
    "InferenceTask": ".inference",
    "MatchingResultTask": ".matching_result",
    "PublishResultTask": ".publish",
    "StreamingTask": ".streaming",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
