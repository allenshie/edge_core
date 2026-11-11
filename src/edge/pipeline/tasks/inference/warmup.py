"""模型暖機（spec-edge.md 3.4）。

啟動時對每個模型用一張空白畫面推理一次，避免第一次推理太久被誤判為卡住。
暖機直接呼叫模型，不經過 Engine 的 process()，所以不會動到排程狀態、快取與 last_results。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
from smart_workflow import TaskError

LOGGER = logging.getLogger(__name__)

DEFAULT_WARMUP_SIZE = 640


def _frame_size(imgsz: Any) -> tuple[int, int]:
    if isinstance(imgsz, bool):
        return DEFAULT_WARMUP_SIZE, DEFAULT_WARMUP_SIZE
    if isinstance(imgsz, (int, float)) and imgsz > 0:
        return int(imgsz), int(imgsz)
    if isinstance(imgsz, (list, tuple)) and len(imgsz) == 2 and all(isinstance(v, (int, float)) and v > 0 for v in imgsz):
        return int(imgsz[0]), int(imgsz[1])
    return DEFAULT_WARMUP_SIZE, DEFAULT_WARMUP_SIZE


def blank_frame(model: Any) -> np.ndarray:
    """空白畫面：大小取模型設定的 imgsz（int 或 [h, w]），沒有設定時用 640×640。"""
    config = getattr(model, "config", None)
    imgsz = config.get("imgsz") if isinstance(config, Mapping) else None
    height, width = _frame_size(imgsz)
    return np.zeros((height, width, 3), dtype=np.uint8)


def warm_up_model(model: Any, frame: Any, metadata: dict[str, Any]) -> None:
    """模型有 warmup() 時呼叫它（例如 YOLO 用 predict，不經過 tracker）；否則沿用 run()。"""
    warmup = getattr(model, "warmup", None)
    if callable(warmup):
        warmup(frame, metadata)
    else:
        model.run(frame, metadata=metadata)


def run_model_warmups(entries: Iterable[tuple[str, str, Any]], *, camera_id: str = "unknown") -> int:
    """依序暖機 (時段, 名稱, 模型)；每個模型記一行 INFO，最後記總耗時。失敗時拋出 TaskError。"""
    started = time.perf_counter()
    count = 0
    for phase, name, model in entries:
        model_started = time.perf_counter()
        try:
            warm_up_model(model, blank_frame(model), {"phase": phase, "camera_id": camera_id})
        except Exception as exc:  # noqa: BLE001 - 暖機失敗就是啟動失敗
            raise TaskError(f"模型暖機失敗：時段={phase} 模型={name}：{exc}") from exc
        count += 1
        LOGGER.info(
            "模型暖機完成：時段=%s 模型=%s 耗時=%.0f ms",
            phase,
            name,
            (time.perf_counter() - model_started) * 1000.0,
        )
    LOGGER.info("模型暖機全部完成：%d 個模型，總耗時 %.0f ms", count, (time.perf_counter() - started) * 1000.0)
    return count


__all__ = ["DEFAULT_WARMUP_SIZE", "blank_frame", "run_model_warmups", "warm_up_model"]
