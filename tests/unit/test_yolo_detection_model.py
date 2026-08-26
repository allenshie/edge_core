from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from edge.pipeline.tasks.inference.models import detection as detection_module
from edge.pipeline.tasks.inference.models.detection import YoloDetectionModel


def _make_model(monkeypatch, config: dict) -> tuple[YoloDetectionModel, Mock, list[Mock]]:
    fake_model = Mock()
    trackers = [Mock(), Mock()]
    fake_model.predictor = SimpleNamespace(trackers=trackers)
    fake_model.track.return_value = []
    fake_model.predict.return_value = []
    monkeypatch.setattr(YoloDetectionModel, "_load_model", lambda self: fake_model)

    model = YoloDetectionModel(
        name="detect_and_track",
        weights_path="fake.pt",
        config_loader=lambda _name: config,
    )
    return model, fake_model, trackers


def test_track_reset_runs_before_track_after_interval(monkeypatch) -> None:
    model, fake_model, trackers = _make_model(
        monkeypatch,
        {"infer_mode": "track", "track_reset_interval_seconds": 10},
    )
    now = [100.0]
    events: list[str] = []
    monkeypatch.setattr(detection_module.time, "monotonic", lambda: now[0])
    for tracker in trackers:
        tracker.reset.side_effect = lambda: events.append("reset")
    fake_model.track.side_effect = lambda *args, **kwargs: (events.append("track") or [])

    model.run("frame", metadata=None)
    now[0] = 110.0
    model.run("frame", metadata=None)

    assert events == ["track", "reset", "reset", "track"]
    assert fake_model.track.call_count == 2
    for tracker in trackers:
        tracker.reset.assert_called_once_with()


def test_track_reset_does_not_run_before_interval(monkeypatch) -> None:
    model, fake_model, trackers = _make_model(
        monkeypatch,
        {"infer_mode": "track", "track_reset_interval_seconds": 10},
    )
    now = [100.0]
    monkeypatch.setattr(detection_module.time, "monotonic", lambda: now[0])

    model.run("frame", metadata=None)
    now[0] = 109.9
    model.run("frame", metadata=None)

    for tracker in trackers:
        tracker.reset.assert_not_called()
    assert fake_model.track.call_count == 2


def test_predict_mode_does_not_reset_or_track(monkeypatch) -> None:
    model, fake_model, trackers = _make_model(
        monkeypatch,
        {"infer_mode": "predict", "track_reset_interval_seconds": 1},
    )

    model.run("frame", metadata=None)

    fake_model.predict.assert_called_once()
    fake_model.track.assert_not_called()
    for tracker in trackers:
        tracker.reset.assert_not_called()


def test_zero_interval_disables_track_reset(monkeypatch) -> None:
    model, fake_model, trackers = _make_model(
        monkeypatch,
        {"infer_mode": "track", "track_reset_interval_seconds": 0},
    )
    monkeypatch.setattr(detection_module.time, "monotonic", lambda: 1000.0)

    for _ in range(3):
        model.run("frame", metadata=None)

    assert fake_model.track.call_count == 3
    for tracker in trackers:
        tracker.reset.assert_not_called()


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "invalid"])
def test_invalid_track_reset_interval_is_rejected(monkeypatch, value) -> None:
    monkeypatch.setattr(YoloDetectionModel, "_load_model", lambda self: Mock())

    with pytest.raises(ValueError, match="track_reset_interval_seconds"):
        YoloDetectionModel(
            name="detect_and_track",
            config_loader=lambda _name: {"track_reset_interval_seconds": value},
        )
