"""Messaging client provider for edge runtime."""
from __future__ import annotations

import os

from smart_messaging_core import HttpConfig, MessagingClient, MessagingConfig, MqttConfig

from edge.config import EdgeConfig

MESSAGING_CLIENT_RESOURCE = "messaging_client"


class MessagingClientProvider:
    """Build MessagingClient from edge config and environment variables."""

    def __init__(self, config: EdgeConfig) -> None:
        self._config = config

    def build(self) -> MessagingClient:
        publish_backend = (os.environ.get("EDGE_PUBLISH_BACKEND") or "http").strip().lower()
        subscribe_backend = self._resolve_subscribe_backend()

        self._validate_backend("publish", publish_backend, {"http", "mqtt", "none"})
        self._validate_backend("subscribe", subscribe_backend, {"mqtt", "none"})

        mqtt_cfg = self._config.mqtt
        mqtt = MqttConfig(
            host=mqtt_cfg.host,
            port=mqtt_cfg.port,
            qos=mqtt_cfg.qos,
            retain=False,
            client_id=mqtt_cfg.client_id,
        )

        http = HttpConfig(
            base_url=self._config.integration.api_base,
            timeout_seconds=self._config.integration.timeout_seconds,
        )
        topic_routes = {"edge/events": "/edge/events"}
        client_cfg = MessagingConfig(
            publish_backend=publish_backend,
            subscribe_backend=subscribe_backend,
            mqtt=mqtt,
            http=http,
            topic_routes=topic_routes,
        )
        return MessagingClient(client_cfg)

    def _resolve_subscribe_backend(self) -> str:
        raw = os.environ.get("EDGE_SUBSCRIBE_BACKEND")
        if raw:
            return raw.strip().lower()
        return "mqtt" if self._config.mqtt.enabled else "none"

    @staticmethod
    def _validate_backend(kind: str, backend: str, allowed: set[str]) -> None:
        if backend in allowed:
            return
        allowed_text = ",".join(sorted(allowed))
        raise ValueError(f"unsupported {kind} backend: {backend}; allowed={allowed_text}")
