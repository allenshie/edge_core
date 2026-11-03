from __future__ import annotations

from edge.config import MqttConfig


def test_qos_defaults_to_zero(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_MQTT_QOS", raising=False)

    assert MqttConfig().qos == 0


def test_qos_can_be_overridden_by_env(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_MQTT_QOS", "1")

    assert MqttConfig().qos == 1
