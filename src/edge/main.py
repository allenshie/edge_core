from __future__ import annotations

import logging
import os
import sys

from smart_messaging_core import MessagingClient, MessagingConfig, MqttConfig
from smart_workflow import MonitoringClient, TaskContext, WorkflowRunner

from edge.config import EdgeConfig, load_config
from edge.pipeline import build_edge_workflow
from edge.api.mode_server import start_mode_server, MODE_RESOURCE


def setup_logging() -> None:
    level_name = os.environ.get("EDGE_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or "INFO"
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def main() -> None:
    setup_logging()
    logger = logging.getLogger("edge")

    config: EdgeConfig = load_config()
    monitor = MonitoringClient(
        config.monitor_endpoint,
        service_name=config.monitor_service_name,
    )

    context = TaskContext(
        logger=logger,
        config=config,
        monitor=monitor,
    )
    default_mode = os.environ.get("EDGE_MODE_DEFAULT")
    context.set_resource(MODE_RESOURCE, default_mode)
    if config.mode_server_enabled:
        start_mode_server(config.mode_server_host, config.mode_server_port, context)
    _start_messaging_subscriber(config, context)

    workflow = build_edge_workflow()
    runner = WorkflowRunner(
        context=context,
        workflow=workflow,
        loop_interval=config.poll_interval,
        retry_backoff=config.retry_backoff,
    )
    runner.run()


def _start_messaging_subscriber(config: EdgeConfig, context: TaskContext) -> None:
    mqtt_cfg = config.mqtt
    if not mqtt_cfg.enabled:
        return
    client = MessagingClient(
        MessagingConfig(
            publish_backend="none",
            subscribe_backend="mqtt",
            mqtt=MqttConfig(
                host=mqtt_cfg.host,
                port=mqtt_cfg.port,
                qos=mqtt_cfg.qos,
                retain=True,
                client_id=mqtt_cfg.client_id,
            ),
        )
    )

    def _on_phase(payload: dict) -> None:
        if os.environ.get("EDGE_MODE_STRATEGY", "external").lower() != "external":
            return
        mode = (payload.get("phase") or payload.get("mode") or "").strip().lower()
        if not mode:
            return
        context.set_resource(MODE_RESOURCE, mode)
        context.logger.info("MQTT mode update: %s", mode)

    try:
        client.subscribe(mqtt_cfg.topic, _on_phase)
    except Exception as exc:  # pylint: disable=broad-except
        context.logger.warning("MQTT subscribe failed; continue without broker: %s", exc)


if __name__ == "__main__":
    main()
