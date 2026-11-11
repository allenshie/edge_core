"""spec-edge.md 3.4：模型暖機。

真實 tracker 的追蹤結果無法用假模型驗證，留給 D1 情境 18 用真實模型驗證。
"""
from __future__ import annotations

import logging

import numpy as np
import pytest
from smart_workflow import TaskError

from edge.pipeline.tasks.inference import engine as engine_module
from edge.pipeline.tasks.inference.engine import DefaultInferenceEngine
from edge.pipeline.tasks.inference.models.detection import YoloDetectionModel
from edge.pipeline.tasks.inference.scheduled_engine.models import ScheduledModelTask
from edge.pipeline.tasks.inference.task import InferenceTask
from edge.pipeline.tasks.inference.warmup import blank_frame
from edge.schema import EdgeDetection

from test_engine_results import _scheduled_engine

FRAME = np.full((8, 8, 3), 7, dtype=np.uint8)


class StatefulModel:
    """每次 run 的結果依呼叫次數而定；沒有 warmup()，暖機會呼叫 run。"""

    def __init__(self, name: str, imgsz=None) -> None:
        self.name = name
        self.config = {} if imgsz is None else {"imgsz": imgsz}
        self.frames: list[tuple] = []

    def run(self, frame, metadata):
        self.frames.append(frame.shape)
        return [EdgeDetection(track_id=len(self.frames), class_name=self.name, bbox=[0, 0, 1, 1], bbox_confidence_score=1.0)]


class WarmupAwareModel(StatefulModel):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.warmups: list[tuple] = []

    def warmup(self, frame, metadata):
        self.warmups.append((frame.shape, int(frame.max()), metadata["phase"]))


@pytest.mark.parametrize(
    ("config", "shape"),
    [({"imgsz": 320}, (320, 320, 3)), ({"imgsz": [384, 640]}, (384, 640, 3)), ({}, (640, 640, 3)), (None, (640, 640, 3))],
)
def test_blank_frame_uses_model_imgsz(config, shape) -> None:
    model = type("M", (), {"config": config})()

    frame = blank_frame(model)

    assert frame.shape == shape
    assert frame.dtype == np.uint8
    assert frame.max() == 0


def test_every_model_of_every_phase_is_warmed_once() -> None:
    a, b, c = WarmupAwareModel("a"), WarmupAwareModel("b"), WarmupAwareModel("c")
    engine = _scheduled_engine(
        {
            "p1": [ScheduledModelTask("a", "every_frame", 0.0, a), ScheduledModelTask("b", "interval", 5.0, b)],
            "p2": [ScheduledModelTask("c", "every_frame", 0.0, c), ScheduledModelTask("r", "replay_last", 0.0, None, source_task="a")],
            "p3": [],
        }
    )

    engine.warmup()

    assert a.warmups == [((640, 640, 3), 0, "p1")]
    assert b.warmups == [((640, 640, 3), 0, "p1")]
    assert c.warmups == [((640, 640, 3), 0, "p2")]


def test_model_without_warmup_runs_blank_frame() -> None:
    model = StatefulModel("m", imgsz=[16, 32])
    engine = _scheduled_engine({"p": [ScheduledModelTask("m", "every_frame", 0.0, model)]})

    engine.warmup()

    assert model.frames == [(16, 32, 3)]


def test_warmup_does_not_touch_schedule_state() -> None:
    model = WarmupAwareModel("m")
    task = ScheduledModelTask("m", "interval", 5.0, model)
    engine = _scheduled_engine({"p": [task]})

    engine.warmup()

    assert task.last_run is None
    assert task.last_results == []
    assert task.run_once_completed is False
    assert engine._cached_results == {}
    assert engine._active_phase is None


def test_results_after_warmup_match_results_without_warmup() -> None:
    def outcomes(warm: bool):
        model = WarmupAwareModel("m")
        engine = _scheduled_engine(
            {"p": [ScheduledModelTask("m", "every_frame", 0.0, model), ScheduledModelTask("i", "interval", 3600.0, WarmupAwareModel("i"))]}
        )
        if warm:
            engine.warmup()
        return [engine.process(FRAME, phase="p") for _ in range(3)]

    assert outcomes(True) == outcomes(False)


def test_warmup_logs_each_model_and_total(caplog) -> None:
    engine = _scheduled_engine({"p": [ScheduledModelTask("a", "every_frame", 0.0, WarmupAwareModel("a"))], "q": [ScheduledModelTask("b", "every_frame", 0.0, WarmupAwareModel("b"))]})

    with caplog.at_level(logging.INFO):
        engine.warmup()

    lines = [r.getMessage() for r in caplog.records if "暖機" in r.getMessage()]
    assert len(lines) == 3
    assert "時段=p" in lines[0] and "模型=a" in lines[0] and "ms" in lines[0]
    assert "時段=q" in lines[1] and "模型=b" in lines[1]
    assert "2 個模型" in lines[2] and "總耗時" in lines[2]


def test_warmup_failure_raises_task_error() -> None:
    class Broken(WarmupAwareModel):
        def warmup(self, frame, metadata):
            raise RuntimeError("CUDA out of memory")

    engine = _scheduled_engine({"p": [ScheduledModelTask("bad", "every_frame", 0.0, Broken("bad"))]})

    with pytest.raises(TaskError, match="模型暖機失敗.*bad.*CUDA out of memory"):
        engine.warmup()


# ---------------------------------------------------------------- YOLO 模型


class FakeUltralytics:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.predictor = None

    def predict(self, frame, **kwargs):
        self.calls.append(("predict", frame.shape, kwargs))
        return []

    def track(self, frame, **kwargs):
        self.calls.append(("track", frame.shape, kwargs))
        return []


def _yolo(config: dict) -> YoloDetectionModel:
    model = YoloDetectionModel("det", weights_path=None, device="cuda:0", config_loader=lambda name: dict(config))
    model._model = FakeUltralytics()
    return model


def test_yolo_warmup_uses_predict_with_production_kwargs_and_no_tracker() -> None:
    model = _yolo({"conf": 0.3, "iou": 0.5, "imgsz": 320, "classes": [0, 2], "verbose": False,
                   "tracker": "bytetrack.yaml", "track_reset_interval_seconds": 3600})

    model.warmup(blank_frame(model), {"phase": "p"})

    assert model._model.calls == [("predict", (320, 320, 3), model._build_predict_kwargs())]
    # 沒有 GPU 的測試環境會把 cuda:0 正規化成 cpu；暖機用的是同一個 device。
    assert model._build_predict_kwargs() == {"device": model.device, "conf": 0.3, "iou": 0.5, "classes": [0, 2], "verbose": False, "imgsz": 320}
    assert model._last_track_reset_monotonic is None


def test_yolo_inference_after_warmup_still_tracks_with_persist() -> None:
    model = _yolo({"imgsz": 320, "tracker": "bytetrack.yaml"})
    model.warmup(blank_frame(model), {"phase": "p"})

    model.run(FRAME, {"phase": "p"})

    name, _, kwargs = model._model.calls[-1]
    assert name == "track"
    assert kwargs["persist"] is True and kwargs["tracker"] == "bytetrack.yaml"


def test_yolo_without_weights_skips_warmup() -> None:
    model = YoloDetectionModel("det", weights_path=None, config_loader=lambda name: {})

    model.warmup(blank_frame(model), {"phase": "p"})


def test_default_engine_warmup_loads_model_and_predicts(monkeypatch) -> None:
    fake = FakeUltralytics()
    fake.to = lambda device: None
    monkeypatch.setattr(engine_module, "YOLO", lambda path: fake)
    engine = DefaultInferenceEngine(None)
    engine._model_config = type("Cfg", (), {"weights_path": "w.pt", "device": None, "confidence_threshold": 0.5,
                                            "resolve_tracker_config": lambda self, root: None})()

    engine.warmup()

    assert fake.calls == [("predict", (640, 640, 3), {"verbose": False})]


def test_inference_task_warmup_delegates_to_engine() -> None:
    task = InferenceTask(None)
    calls: list[str] = []
    task._engine.warmup = lambda: calls.append("warm")

    task.warmup()

    assert calls == ["warm"]
