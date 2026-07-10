from __future__ import annotations

import os
from pathlib import Path

from edge.config import EdgeConfig
from edge.main import build_context
from edge.messaging import MESSAGING_CLIENT_RESOURCE, PHASE_UPDATES_ROUTE
from edge.runtime.messaging_runtime import MessagingClientProvider, init_messaging_client, start_messaging_subscriber


ROOT = Path(__file__).resolve().parents[3]
CAM01_TEMPLATE = ROOT / "edge_core" / "env" / ".env.cam01.example"


class _FakeMessagingClient:
    def __init__(self) -> None:
        self.subscriptions: dict[str, object] = {}
        self.closed = False

    def subscribe(self, route_key: str, callback) -> None:
        self.subscriptions[route_key] = callback

    def close(self) -> None:
        self.closed = True


def _load_template_env(monkeypatch, path: Path) -> None:
    for key in list(os.environ):
        if key.startswith(("EDGE_", "INTEGRATION_", "MONITOR_", "INFERENCE_", "PUBLISH_", "STREAMING_")):
            monkeypatch.delenv(key, raising=False)

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        monkeypatch.setenv(key.strip(), value)


def test_bootstrap_smoke_uses_cam01_template(monkeypatch) -> None:
    _load_template_env(monkeypatch, CAM01_TEMPLATE)

    config = EdgeConfig()
    assert config.camera.camera_id == "cam01"
    assert config.monitor_service_name == "edge-cam01"
    assert config.streaming.url.endswith("cam01")

    context = build_context(config)
    assert context.get_resource("edge_mode") == "working_stage_1"

    fake_client = _FakeMessagingClient()
    monkeypatch.setattr(MessagingClientProvider, "build", lambda self: fake_client)

    messaging_client = init_messaging_client(context, context.logger)
    start_messaging_subscriber(context)

    assert context.get_resource(MESSAGING_CLIENT_RESOURCE) is messaging_client
    assert set(messaging_client.subscriptions) == {PHASE_UPDATES_ROUTE}

    messaging_client.subscriptions[PHASE_UPDATES_ROUTE]({"phase": "working_stage_2"})
    assert context.get_resource("edge_mode") == "working_stage_2"
