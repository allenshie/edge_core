"""Edge workflow definitions."""
from __future__ import annotations

from importlib import import_module
from typing import Any

from smart_workflow import Workflow

__all__ = [
    "build_edge_workflow",
    "EdgePipeline",
    "InitPipelineTask",
    "PipelineScheduler",
]


def _pipeline_module():
    return import_module(".pipeline", __name__)


def build_edge_workflow() -> Workflow:
    pipeline = _pipeline_module()
    workflow = Workflow()
    workflow.add_startup_task(lambda: pipeline.InitPipelineTask())
    workflow.set_loop(lambda: pipeline.PipelineScheduler())
    return workflow


def __getattr__(name: str) -> Any:
    if name in {"EdgePipeline", "InitPipelineTask", "PipelineScheduler"}:
        value = getattr(_pipeline_module(), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
