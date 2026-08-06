from __future__ import annotations

from edge.config import EdgeConfig
from edge.messaging import MESSAGING_CLIENT_RESOURCE, MessagingClientProvider
from edge.pipeline.tasks.publish.engine import MessagingPublishEngine
from edge.runtime.messaging_runtime import init_messaging_client, start_messaging_subscriber


class _NullLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _DummyContext:
    def __init__(self, config: EdgeConfig) -> None:
        self.config = config
        self.logger = _NullLogger()
        self._resources: dict[str, object] = {}

    def set_resource(self, key: str, value) -> None:
        self._resources[key] = value

    def get_resource(self, key: str, default=None):
        return self._resources.get(key, default)


class _FakeMessagingClient:
    def __init__(self, client_id: str | None) -> None:
        self.client_id = client_id
        self.close_calls = 0
        self.subscriptions: dict[str, object] = {}
        self.published: list[tuple[str, dict]] = []

    def close(self) -> None:
        self.close_calls += 1

    def subscribe(self, route_key: str, callback) -> None:
        self.subscriptions[route_key] = callback

    def publish(self, route_key: str, payload: dict) -> bool:
        self.published.append((route_key, payload))
        return True


def _context(monkeypatch, client_id: str | None) -> _DummyContext:
    if client_id is None:
        monkeypatch.delenv("EDGE_MQTT_CLIENT_ID", raising=False)
    else:
        monkeypatch.setenv("EDGE_MQTT_CLIENT_ID", client_id)
    return _DummyContext(EdgeConfig())


def test_publish_engine_reuses_shared_client(monkeypatch):
    context = _context(monkeypatch, "smart-intersection-edge-cam01")
    shared_client = _FakeMessagingClient(context.config.mqtt.client_id)
    context.set_resource(MESSAGING_CLIENT_RESOURCE, shared_client)

    def fail_if_build_called(self):
        raise AssertionError("publish engine must not build a second messaging client")

    monkeypatch.setattr(MessagingClientProvider, "build", fail_if_build_called)

    engine = MessagingPublishEngine(context)

    assert engine._client is shared_client
    assert engine._is_shared_client is True


def test_publish_engine_does_not_close_shared_client(monkeypatch):
    context = _context(monkeypatch, "smart-intersection-edge-cam01")
    shared_client = _FakeMessagingClient(context.config.mqtt.client_id)
    context.set_resource(MESSAGING_CLIENT_RESOURCE, shared_client)

    engine = MessagingPublishEngine(context)
    records = engine.close()

    assert shared_client.close_calls == 0
    assert records[0]["state"] == "skipped"
    assert records[0]["detail"] == "shared client retained"


def test_publish_engine_builds_fallback_when_shared_client_is_missing(monkeypatch):
    context = _context(monkeypatch, "smart-intersection-edge-cam01")
    fallback_client = _FakeMessagingClient(context.config.mqtt.client_id)
    build_calls = 0

    def build_fallback(self):
        nonlocal build_calls
        build_calls += 1
        return fallback_client

    monkeypatch.setattr(MessagingClientProvider, "build", build_fallback)

    engine = MessagingPublishEngine(context)

    assert engine._client is fallback_client
    assert engine._is_shared_client is False
    assert build_calls == 1
    assert fallback_client.client_id == "smart-intersection-edge-cam01"


def test_publish_engine_closes_fallback_client(monkeypatch):
    context = _context(monkeypatch, "smart-intersection-edge-cam01")
    fallback_client = _FakeMessagingClient(context.config.mqtt.client_id)
    monkeypatch.setattr(MessagingClientProvider, "build", lambda self: fallback_client)

    engine = MessagingPublishEngine(context)
    records = engine.close()

    assert fallback_client.close_calls == 1
    assert records[0]["state"] == "done"
    assert records[0]["detail"] == "messaging client closed"


def test_unset_client_id_keeps_fallback_compatibility(monkeypatch):
    context = _context(monkeypatch, None)
    fallback_client = _FakeMessagingClient(context.config.mqtt.client_id)
    monkeypatch.setattr(MessagingClientProvider, "build", lambda self: fallback_client)

    engine = MessagingPublishEngine(context)

    assert context.config.mqtt.client_id is None
    assert engine._client is fallback_client
    assert fallback_client.client_id is None


def test_explicit_client_id_uses_one_client_for_runtime_subscriber_and_publish(monkeypatch):
    context = _context(monkeypatch, "smart-intersection-edge-cam01")
    created_clients: list[_FakeMessagingClient] = []

    def build_client(self):
        client = _FakeMessagingClient(self._config.mqtt.client_id)
        created_clients.append(client)
        return client

    monkeypatch.setattr(MessagingClientProvider, "build", build_client)

    shared_client = init_messaging_client(context, context.logger)
    start_messaging_subscriber(context)
    engine = MessagingPublishEngine(context)
    outcome = engine.publish([])

    assert len(created_clients) == 1
    assert shared_client is created_clients[0]
    assert context.get_resource(MESSAGING_CLIENT_RESOURCE) is shared_client
    assert engine._client is shared_client
    assert engine._is_shared_client is True
    assert shared_client.subscriptions
    assert outcome.status == 200
    assert len(shared_client.published) == 1
