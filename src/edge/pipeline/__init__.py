"""Edge workflow definitions."""
from __future__ import annotations

from smart_workflow import Workflow

from edge.pipeline.pipeline import EdgePipeline, InitPipelineTask, PipelineScheduler


def build_edge_workflow() -> Workflow:
    workflow = Workflow()
    workflow.add_startup_task(lambda: InitPipelineTask())
    workflow.set_loop(lambda: PipelineScheduler())
    return workflow


__all__ = [
    "build_edge_workflow",
    "EdgePipeline",
    "InitPipelineTask",
    "PipelineScheduler",
]
