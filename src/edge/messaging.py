"""Messaging client provider for edge runtime."""
from __future__ import annotations

from smart_messaging_core import HttpConfig, MessagingClient, MessagingConfig, MqttConfig, RouteConfig

from edge.config import EdgeConfig

MESSAGING_CLIENT_RESOURCE = "messaging_client"
EDGE_EVENTS_ROUTE = "edge_events"
PHASE_UPDATES_ROUTE = "phase_updates"


class MessagingClientProvider:
    """Build MessagingClient from edge config."""

    def __init__(self, config: EdgeConfig) -> None:
        self._config = config

    def build(self) -> MessagingClient:
        mqtt_cfg = self._config.mqtt
        mqtt = MqttConfig(
            host=mqtt_cfg.host,
            port=mqtt_cfg.port,
            qos=mqtt_cfg.qos,
            retain=False,
            client_id=mqtt_cfg.client_id,
            auth_enabled=mqtt_cfg.auth_enabled,
            username=mqtt_cfg.username,
            password=mqtt_cfg.password,
        )

        http_cfg = self._config.http_messaging
        http = HttpConfig(
            base_url=self._config.integration.api_base,
            timeout_seconds=self._config.integration.timeout_seconds,
            listen_host=http_cfg.listen_host,
            listen_port=http_cfg.listen_port,
        )

        routes: dict[str, RouteConfig] = {}

        events_route = resolve_events_route(self._config)
        if events_route is not None:
            routes[EDGE_EVENTS_ROUTE] = RouteConfig(*events_route)

        phase_route = resolve_phase_updates_route(self._config)
        if phase_route is not None:
            routes[PHASE_UPDATES_ROUTE] = RouteConfig(*phase_route)

        return MessagingClient(MessagingConfig(mqtt=mqtt, http=http, routes=routes))


def resolve_events_route(config: EdgeConfig) -> tuple[str, str] | None:
    events_cfg = config.edge_events
    if events_cfg.backend == "none":
        return None
    _validate_backend("events", events_cfg.backend, {"http", "mqtt", "none"})
    return events_cfg.backend, events_cfg.channel



def resolve_phase_updates_route(config: EdgeConfig) -> tuple[str, str] | None:
    phase_cfg = config.phase_messaging
    if phase_cfg.backend == "none":
        return None
    _validate_backend("phase", phase_cfg.backend, {"http", "mqtt", "none"})
    return phase_cfg.backend, phase_cfg.channel



def _validate_backend(kind: str, backend: str, allowed: set[str]) -> None:
    if backend in allowed:
        return
    allowed_text = ",".join(sorted(allowed))
    raise ValueError(f"unsupported {kind} backend: {backend}; allowed={allowed_text}")
