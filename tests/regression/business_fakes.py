"""業務輸出回歸用的假元件：取流、模型與 messaging client。

只使用導入健康回報之前（edge_core 5422b1e）就存在的介面，
讓同一份 harness 可以在基準環境錄製、在新版環境比對。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np

from edge.pipeline.tasks.ingestion.engines import BaseIngestionEngine
from edge.schema import EdgeDetection

BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)

# 每一項是一次 fetch()：(frame_seq, 畫面數值, 這次 fetch 前要送出的 phase)。
# 同一個 frame_seq 連續出現代表沒有新畫面；phase 為 None 代表不切換。
SCRIPT: list[tuple[int, int, str | None]] = [
    (1, 10, None),
    (2, 20, None),
    (2, 20, None),
    (3, 30, None),
    (4, 40, "beta"),
    (5, 50, None),
    (5, 50, None),
    (6, 60, "gamma"),
    (7, 70, None),
    (8, 80, "alpha"),
    (9, 90, None),
    (10, 100, None),
]


class Recorder:
    """收集整次執行的觀察結果，由 harness 讀取。"""

    def __init__(self) -> None:
        self.fetch_index = 0
        self.client_config: Any = None
        self.publishes: list[dict[str, Any]] = []
        self.model_calls: list[list[Any]] = []
        self.subscriptions: dict[str, Callable[[dict], None]] = {}


RECORDER = Recorder()


def reset_recorder() -> Recorder:
    global RECORDER
    RECORDER = Recorder()
    return RECORDER


class FakeIngestionEngine(BaseIngestionEngine):
    """依 SCRIPT 依序回傳畫面；用完時拋出 KeyboardInterrupt，讓 runner 正常結束。"""

    source_label = "fake"

    def __init__(self, context: Any = None, **kwargs: Any) -> None:
        super().__init__(context, **kwargs)
        self._position = 0

    def fetch(self) -> dict[str, Any]:
        if self._position >= len(SCRIPT):
            raise KeyboardInterrupt
        frame_seq, value, phase = SCRIPT[self._position]
        self._position += 1
        RECORDER.fetch_index = self._position
        if phase is not None:
            callback = RECORDER.subscriptions.get("phase_updates")
            if callback is not None:
                callback({"phase": phase})
        return {
            "frame": np.full((4, 6, 3), value, dtype=np.uint8),
            "source": self.source_label,
            "capture_ts": BASE_TS + timedelta(seconds=frame_seq),
            "frame_seq": frame_seq,
        }


class _FakeModel:
    """無狀態的假模型：輸出只由畫面數值決定。數值 0（空白畫面）不記錄呼叫。"""

    def __init__(self, name: str, weights_path: str | None = None, label: str | None = None, **_: Any) -> None:
        self.name = name
        self.label = label or name

    def run(self, frame: Any, metadata: Any) -> list[EdgeDetection]:
        value = int(frame[0, 0, 0])
        if value:
            RECORDER.model_calls.append([self.name, value, (metadata or {}).get("phase")])
        return self._detections(value)

    def _detections(self, value: int) -> list[EdgeDetection]:
        raise NotImplementedError


class FakeDetector(_FakeModel):
    def _detections(self, value: int) -> list[EdgeDetection]:
        if value % 30 == 0:
            return []
        return [
            EdgeDetection(
                track_id=value,
                class_name="car",
                bbox=[value, value + 1, value + 10, value + 11],
                bbox_confidence_score=0.9,
                score=0.9,
            ),
            EdgeDetection(
                track_id=value + 1,
                class_name="person",
                bbox=[1, 2, 3, 4],
                bbox_confidence_score=0.75,
                score=0.75,
            ),
        ]


class FakeClassifier(_FakeModel):
    def _detections(self, value: int) -> list[EdgeDetection]:
        return [
            EdgeDetection(
                track_id=None,
                class_name="signal",
                bbox=[0, 0, 1, 1],
                bbox_confidence_score=1.0,
                state="red" if value < 50 else "green",
            )
        ]


class FakeMessagingClient:
    """取代 smart_messaging_core.MessagingClient，記錄設定與每一次 publish。"""

    def __init__(self, config: Any = None) -> None:
        RECORDER.client_config = config

    def subscribe(self, route_key: str, callback: Callable[[dict], None]) -> None:
        RECORDER.subscriptions[route_key] = callback

    def publish(self, route_key: str, payload: dict) -> bool:
        RECORDER.publishes.append({"fetch_index": RECORDER.fetch_index, "route": route_key, "payload": payload})
        return True

    def is_connected(self, route_key: str) -> bool:
        _ = route_key
        return True

    def close(self) -> None:
        return None
