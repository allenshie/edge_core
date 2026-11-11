"""spec-edge.md 第 6 節：健康回報的新設定。"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from smart_workflow import TaskContext, TaskError

from edge.config import EdgeConfig, IngestionConfig, MqttConfig
import edge.messaging as messaging_module
from edge.messaging import MessagingClientProvider
from edge.pipeline.tasks.ingestion import IngestionTask
from edge.pipeline.tasks.ingestion.engines import BaseIngestionEngine, RtspIngestionEngine
from edge.runtime.health_settings import HealthSettings


def test_defaults_when_env_missing() -> None:
    settings = HealthSettings.from_env({})

    assert settings == HealthSettings(
        enabled=True,
        exit_on_failure=True,
        status_host="127.0.0.1",
        status_port=None,
        stale_after_override=None,
        loop_timeout_s=30.0,
    )


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "FALSE", " Off "])
def test_switches_parse_false_values(raw: str) -> None:
    settings = HealthSettings.from_env({"EDGE_HEALTH_ENABLED": raw, "EDGE_HEALTH_EXIT_ON_FAILURE": raw})

    assert settings.enabled is False
    assert settings.exit_on_failure is False


def test_values_are_read_from_env() -> None:
    settings = HealthSettings.from_env(
        {
            "EDGE_HEALTH_ENABLED": "1",
            "EDGE_HEALTH_STATUS_HOST": "0.0.0.0",
            "EDGE_HEALTH_STATUS_PORT": "18081",
            "EDGE_HEALTH_STALE_AFTER_SECONDS": "12.5",
            "EDGE_HEALTH_LOOP_TIMEOUT_SECONDS": "45",
        }
    )

    assert settings.enabled is True
    assert settings.status_host == "0.0.0.0"
    assert settings.status_port == 18081
    assert settings.stale_after_override == 12.5
    assert settings.loop_timeout_s == 45.0


def test_blank_optional_values_mean_unset() -> None:
    settings = HealthSettings.from_env({"EDGE_HEALTH_STATUS_PORT": " ", "EDGE_HEALTH_STALE_AFTER_SECONDS": ""})

    assert settings.status_port is None
    assert settings.stale_after_override is None


def test_mqtt_keepalive_defaults_to_15(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_MQTT_KEEPALIVE", raising=False)

    assert MqttConfig().keepalive == 15


def test_mqtt_keepalive_is_passed_to_messaging_core(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_MQTT_KEEPALIVE", "60")
    # 不建立真的 client（會連線 broker），只取得傳給 smart_messaging_core 的設定。
    monkeypatch.setattr(messaging_module, "MessagingClient", lambda config: SimpleNamespace(config=config))

    client = MessagingClientProvider(EdgeConfig()).build()

    assert isinstance(client.config.mqtt, messaging_module.MqttConfig)
    assert client.config.mqtt.keepalive == 60


class _CustomEngine(BaseIngestionEngine):
    source_label = "custom"

    def __init__(self, context=None) -> None:
        super().__init__(context)
        self.context = context


class _NotAnEngine:
    def __init__(self, context=None) -> None:
        self.context = context


def _context(engine_class: str | None, mode: str = "rtsp") -> TaskContext:
    config = SimpleNamespace(
        ingestion=IngestionConfig(mode=mode),
        ingestion_engine_class=engine_class,
        health_report_interval_seconds=0,
    )
    return TaskContext(logger=logging.getLogger("test"), config=config, monitor=SimpleNamespace())


def test_ingestion_engine_follows_mode_when_class_unset() -> None:
    task = IngestionTask(_context(None))

    assert type(task._engine) is RtspIngestionEngine


@pytest.mark.parametrize("separator", [":", "."])
def test_ingestion_engine_class_overrides_mode(separator: str) -> None:
    context = _context(f"{__name__}{separator}_CustomEngine")

    task = IngestionTask(context)

    assert type(task._engine) is _CustomEngine
    assert task._engine.context is context


def test_ingestion_engine_class_must_inherit_base() -> None:
    with pytest.raises(TaskError, match="BaseIngestionEngine"):
        IngestionTask(_context(f"{__name__}:_NotAnEngine"))
