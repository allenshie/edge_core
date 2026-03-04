from __future__ import annotations

import logging
import os
import sys
from typing import Any

from smart_workflow import MonitoringClient, TaskContext, WorkflowRunner

from edge.api.mode_server import MODE_RESOURCE, start_mode_server
from edge.config import EdgeConfig, load_config
from edge.messaging import MESSAGING_CLIENT_RESOURCE, MessagingClientProvider
from edge.pipeline import build_edge_workflow


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

    messaging_client = MessagingClientProvider(config).build()
    context.set_resource(MESSAGING_CLIENT_RESOURCE, messaging_client)

    if config.mode_server_enabled:
        start_mode_server(config.mode_server_host, config.mode_server_port, context)
    _start_messaging_subscriber(config, context, messaging_client)

    workflow = build_edge_workflow()
    runner = WorkflowRunner(
        context=context,
        workflow=workflow,
        loop_interval=config.poll_interval,
        retry_backoff=config.retry_backoff,
    )
    try:
        runner.run()
    finally:
        _shutdown_messaging_client(messaging_client, logger)


def _start_messaging_subscriber(config: EdgeConfig, context: TaskContext, client: Any) -> None:
    mqtt_cfg = config.mqtt
    if not mqtt_cfg.enabled:
        return

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


def _shutdown_messaging_client(client: Any, logger: logging.Logger) -> None:
    close_fn = getattr(client, "close", None)
    if callable(close_fn):
        try:
            close_fn()
            return
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("messaging client close failed: %s", exc)

    for collection_name in ("_subscribers", "_publishers"):
        entries = getattr(client, collection_name, None)
        if not isinstance(entries, dict):
            continue
        for entry in entries.values():
            raw_client = getattr(entry, "_client", None)
            if raw_client is None:
                continue
            loop_stop = getattr(raw_client, "loop_stop", None)
            disconnect = getattr(raw_client, "disconnect", None)
            try:
                if callable(loop_stop):
                    loop_stop()
                if callable(disconnect):
                    disconnect()
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("messaging transport cleanup failed: %s", exc)


if __name__ == "__main__":
    main()
