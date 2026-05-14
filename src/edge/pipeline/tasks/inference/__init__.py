from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "InferenceTask",
    "BaseInferenceEngine",
    "DefaultInferenceEngine",
    "BaseInferenceModel",
    "ScheduledInferenceEngine",
]

_EXPORTS = {
    "InferenceTask": ".task",
    "BaseInferenceEngine": ".engine",
    "DefaultInferenceEngine": ".engine",
    "BaseInferenceModel": ".model",
    "ScheduledInferenceEngine": ".scheduled",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
