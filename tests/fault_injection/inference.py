"""推理 Engine 的故障：startup_stuck、infer_stuck、infer_error、infer_cuda。"""
from __future__ import annotations

from typing import Any

from edge.pipeline.tasks.inference.engine import InferenceOutcome
from edge.pipeline.tasks.inference.scheduled_engine.engine import ScheduledInferenceEngine

from .flags import block_while, is_set


class FaultInferenceEngine(ScheduledInferenceEngine):
    def warmup(self) -> None:
        block_while("startup_stuck")  # 情境 3：暖機卡住，不會送 READY=1
        super().warmup()

    def process(self, frame: Any, *, phase: str | None = None, metadata: dict[str, Any] | None = None) -> InferenceOutcome:
        if is_set("infer_cuda"):  # 情境 9
            raise RuntimeError("CUDA error: an illegal memory access was encountered (D1 fault)")
        if is_set("infer_error"):  # 情境 8：可恢復的錯誤
            raise RuntimeError("D1 fault: recoverable inference error")
        block_while("infer_stuck")  # 情境 7
        return super().process(frame, phase=phase, metadata=metadata)


__all__ = ["FaultInferenceEngine"]
