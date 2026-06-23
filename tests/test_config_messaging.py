from __future__ import annotations

from edge.config import EdgeConfig
from edge.messaging import resolve_app_inbound_backend, resolve_inbound_route_specs


def test_edge_config_normalizes_shared_inbound_routes(monkeypatch):
    monkeypatch.setenv("EDGE_APP_INBOUND_BACKEND", "HTTP")
    monkeypatch.setenv("EDGE_PHASE_ENABLED", "1")
    monkeypatch.setenv("EDGE_PHASE_CHANNEL", "integration/phase")
    monkeypatch.setenv("EDGE_PHASE_RESOURCE_NAME", " phase_state ")
    monkeypatch.setenv("EDGE_MATCHING_RESULT_ENABLED", "1")
    monkeypatch.setenv("EDGE_MATCHING_RESULT_CHANNEL", "/integration/matching")
    monkeypatch.setenv("EDGE_MATCHING_RESULT_RESOURCE_NAME", " matching_snapshot ")

    config = EdgeConfig()
    phase_route, matching_route = resolve_inbound_route_specs(config)

    assert resolve_app_inbound_backend(config) == "http"
    assert config.app_inbound.backend == "http"
    assert phase_route.enabled is True
    assert phase_route.channel == "/integration/phase"
    assert phase_route.resource_name == "phase_state"
    assert matching_route.enabled is True
    assert matching_route.channel == "/integration/matching"
    assert matching_route.resource_name == "matching_snapshot"


def test_edge_config_defaults_keep_phase_enabled_and_matching_disabled(monkeypatch):
    for name in (
        "EDGE_APP_INBOUND_BACKEND",
        "EDGE_PHASE_ENABLED",
        "EDGE_PHASE_CHANNEL",
        "EDGE_PHASE_RESOURCE_NAME",
        "EDGE_MATCHING_RESULT_ENABLED",
        "EDGE_MATCHING_RESULT_CHANNEL",
        "EDGE_MATCHING_RESULT_RESOURCE_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

    config = EdgeConfig()
    phase_route, matching_route = resolve_inbound_route_specs(config)

    assert config.app_inbound.backend == "mqtt"
    assert phase_route.enabled is True
    assert phase_route.channel == "integration/phase"
    assert phase_route.resource_name == "edge_mode"
    assert matching_route.enabled is False
    assert matching_route.channel == "integration/matching"
    assert matching_route.resource_name == "matching_result_snapshot"
