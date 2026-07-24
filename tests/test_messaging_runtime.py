from __future__ import annotations

from edge.config import EdgeConfig
from edge.messaging import (
    MATCHING_BROADCAST_ROUTE,
    MESSAGING_CLIENT_RESOURCE,
    PHASE_UPDATES_ROUTE,
)
from edge.runtime.messaging_runtime import start_messaging_subscriber


class _NullLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _FakeMessagingClient:
    def __init__(self) -> None:
        self.subscriptions: dict[str, object] = {}

    def subscribe(self, route_key: str, callback) -> None:
        self.subscriptions[route_key] = callback


class _DummyContext:
    def __init__(self, config: EdgeConfig) -> None:
        self.config = config
        self.logger = _NullLogger()
        self._resources: dict[str, object] = {}

    def set_resource(self, key: str, value) -> None:
        self._resources[key] = value

    def get_resource(self, key: str, default=None):
        return self._resources.get(key, default)


def test_start_messaging_subscriber_subscribes_enabled_routes_and_writes_context(monkeypatch):
    monkeypatch.setenv("EDGE_APP_INBOUND_BACKEND", "http")
    monkeypatch.setenv("EDGE_PHASE_ENABLED", "1")
    monkeypatch.setenv("EDGE_PHASE_CHANNEL", "integration/phase")
    monkeypatch.setenv("EDGE_PHASE_RESOURCE_NAME", "phase_state")
    monkeypatch.setenv("EDGE_MATCHING_RESULT_ENABLED", "1")
    monkeypatch.setenv("EDGE_MATCHING_RESULT_CHANNEL", "integration/matching")
    monkeypatch.setenv("EDGE_MATCHING_RESULT_RESOURCE_NAME", "matching_snapshot")
    config = EdgeConfig()
    context = _DummyContext(config)
    client = _FakeMessagingClient()
    context.set_resource(MESSAGING_CLIENT_RESOURCE, client)

    start_messaging_subscriber(context)

    assert set(client.subscriptions) == {PHASE_UPDATES_ROUTE, MATCHING_BROADCAST_ROUTE}

    client.subscriptions[PHASE_UPDATES_ROUTE]({"phase": "working_stage_2"})
    client.subscriptions[MATCHING_BROADCAST_ROUTE](
        {
            "schema_version": 1,
            "message_type": "matching_result",
            "generated_at": "2026-06-23T00:00:00Z",
            "camera_matches": {
                "cam01": [
                    {"local_id": 7, "global_id": "G-7", "class_name": "person"},
                ]
            },
        }
    )

    phase_state = context.get_resource("phase_state")
    matching_snapshot = context.get_resource("matching_snapshot")

    assert phase_state == "working_stage_2"
    assert matching_snapshot["enabled"] is True
    assert matching_snapshot["subscribed"] is True
    assert matching_snapshot["camera_id"] == config.camera.camera_id
    assert matching_snapshot["result_version"] == 1
    assert matching_snapshot["matches"] == 1
    assert matching_snapshot["local_to_global"] == {7: "G-7"}
    assert matching_snapshot["camera_matches"]["cam01"][0]["local_id"] == 7
    assert matching_snapshot["camera_matches"]["cam01"][0]["global_id"] == "G-7"


def test_start_messaging_subscriber_skips_when_all_routes_disabled(monkeypatch):
    monkeypatch.setenv("EDGE_PHASE_ENABLED", "0")
    monkeypatch.setenv("EDGE_MATCHING_RESULT_ENABLED", "0")
    config = EdgeConfig()
    context = _DummyContext(config)
    client = _FakeMessagingClient()
    context.set_resource(MESSAGING_CLIENT_RESOURCE, client)

    start_messaging_subscriber(context)

    assert client.subscriptions == {}
