"""Messaging client provider for edge runtime."""
from __future__ import annotations

from dataclasses import dataclass

from smart_messaging_core import HttpConfig, MessagingClient, MessagingConfig, MqttConfig, RouteConfig

from edge.config import EdgeConfig

MESSAGING_CLIENT_RESOURCE = "messaging_client"
APP_INBOUND_BACKEND = "app_inbound_backend"
EDGE_EVENTS_ROUTE = "edge_events"
PHASE_UPDATES_ROUTE = "phase_updates"
MATCHING_BROADCAST_ROUTE = "matching_broadcast"


@dataclass(frozen=True)
class InboundRouteSpec:
    route_key: str
    enabled: bool
    channel: str
    resource_name: str


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

        inbound_backend = resolve_app_inbound_backend(self._config)
        for spec in resolve_inbound_route_specs(self._config):
            if not spec.enabled:
                continue
            routes[spec.route_key] = RouteConfig(inbound_backend, spec.channel)

        events_route = resolve_events_route(self._config)
        if events_route is not None:
            routes[EDGE_EVENTS_ROUTE] = RouteConfig(*events_route)

        return MessagingClient(MessagingConfig(mqtt=mqtt, http=http, routes=routes))


def resolve_app_inbound_backend(config: EdgeConfig) -> str:
    backend = config.app_inbound.backend
    if backend not in {"http", "mqtt"}:
        raise ValueError(f"unsupported app inbound backend: {backend}; allowed=http,mqtt")
    return backend


def resolve_phase_inbound_route(config: EdgeConfig) -> InboundRouteSpec:
    phase_cfg = config.phase_messaging
    return InboundRouteSpec(
        route_key=PHASE_UPDATES_ROUTE,
        enabled=bool(phase_cfg.enabled),
        channel=phase_cfg.channel,
        resource_name=phase_cfg.resource_name,
    )


def resolve_matching_result_inbound_route(config: EdgeConfig) -> InboundRouteSpec:
    matching_cfg = config.matching_result
    return InboundRouteSpec(
        route_key=MATCHING_BROADCAST_ROUTE,
        enabled=bool(matching_cfg.enabled),
        channel=matching_cfg.channel,
        resource_name=matching_cfg.resource_name,
    )


def resolve_inbound_route_specs(config: EdgeConfig) -> tuple[InboundRouteSpec, InboundRouteSpec]:
    return resolve_phase_inbound_route(config), resolve_matching_result_inbound_route(config)


def resolve_events_route(config: EdgeConfig) -> tuple[str, str] | None:
    events_cfg = config.edge_events
    if events_cfg.backend == "none":
        return None
    _validate_backend("events", events_cfg.backend, {"http", "mqtt", "none"})
    return events_cfg.backend, events_cfg.channel


def _validate_backend(kind: str, backend: str, allowed: set[str]) -> None:
    if backend in allowed:
        return
    allowed_text = ",".join(sorted(allowed))
    raise ValueError(f"unsupported {kind} backend: {backend}; allowed={allowed_text}")
