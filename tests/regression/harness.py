"""業務輸出回歸 harness：用假的取流與推理模型、真的 publish Engine 跑一次 edge.main.run_daemon。

回傳送給 messaging client 的 topic、MQTT 設定與 payload，供錄製基準與比對使用。
只使用導入健康回報之前就存在的介面，基準環境與新版環境共用同一份程式碼。
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
BASELINE_PATH = DATA / "business_baseline.json"

# 由 harness 控制的 env 前綴；執行前先移除外部環境中的同名設定，避免影響結果。
_CONTROLLED_PREFIXES = ("EDGE_", "INFERENCE_", "PUBLISH_", "STREAMING_", "INGESTION_", "MONITOR_", "INTEGRATION_")

BASE_ENV = {
    "EDGE_CAMERA_ID": "cam-regress",
    "EDGE_INGEST_MODE": "file",
    "EDGE_FILE_PATH": str(DATA / "unused.mp4"),
    "EDGE_POLL_INTERVAL": "0",
    "EDGE_RETRY_BACKOFF": "1",
    "EDGE_MODE_DEFAULT": "alpha",
    "EDGE_APP_INBOUND_BACKEND": "mqtt",
    "EDGE_PHASE_ENABLED": "1",
    "EDGE_PHASE_CHANNEL": "integration/phase",
    "EDGE_MATCHING_RESULT_ENABLED": "0",
    "EDGE_EVENTS_BACKEND": "mqtt",
    "EDGE_EVENTS_CHANNEL": "edge/cam-regress/events",
    "EDGE_MQTT_HOST": "broker.invalid",
    "EDGE_MQTT_PORT": "18830",
    "EDGE_MQTT_QOS": "1",
    "EDGE_STREAMING_ENABLED": "0",
    "EDGE_PUBLISH_ENABLED": "1",
    "EDGE_HEALTH_SERVER_ENABLED": "0",
    "EDGE_RESOURCE_ROOT": str(DATA),
    "EDGE_SCHEDULE_PATH": str(DATA / "schedule.json"),
    "INFERENCE_ENGINE_CLASS": "edge.pipeline.tasks.inference.scheduled:ScheduledInferenceEngine",
}

_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def _build_env(overrides: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(_CONTROLLED_PREFIXES)}
    env.update(BASE_ENV)
    env.update(overrides)
    return env


def _normalize(publishes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """事件時間（EdgeEvent.timestamp）與 session id 每次執行都不同，以固定字串取代。"""
    sessions: dict[str, str] = {}
    normalized = []
    for item in publishes:
        payload = json.loads(json.dumps(item["payload"], default=str))
        stamp = payload.get("timestamp")
        if isinstance(stamp, str) and _ISO_TS.match(stamp):
            payload["timestamp"] = "<timestamp>"
        session = payload.get("session_id")
        if isinstance(session, str):
            payload["session_id"] = sessions.setdefault(session, f"<session-{len(sessions) + 1}>")
        normalized.append({"fetch_index": item["fetch_index"], "route": item["route"], "payload": payload})
    return normalized


def _describe_config(config: Any) -> dict[str, Any]:
    mqtt = getattr(config, "mqtt", None)
    routes = getattr(config, "routes", None) or {}
    mqtt_fields = {}
    for name in ("host", "port", "qos", "retain", "client_id", "auth_enabled", "username", "password"):
        mqtt_fields[name] = getattr(mqtt, name, None)
    return {
        "mqtt": mqtt_fields,
        "routes": {key: [route.backend, route.channel] for key, route in sorted(routes.items())},
    }


def run_harness(env_overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """執行一次完整的 run_daemon，回傳正規化後的業務輸出。另外回傳 ``_exit_calls`` 與 ``_keepalive``（不列入比對）。"""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import business_fakes

    recorder = business_fakes.reset_recorder()
    exit_calls: list[int] = []
    with ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, _build_env(env_overrides or {}), clear=True))
        ingestion_task = importlib.import_module("edge.pipeline.tasks.ingestion.task")
        stack.enter_context(mock.patch.dict(ingestion_task.ENGINE_BY_MODE, {"file": business_fakes.FakeIngestionEngine}))
        messaging = importlib.import_module("edge.messaging")
        stack.enter_context(mock.patch.object(messaging, "MessagingClient", business_fakes.FakeMessagingClient))
        try:
            bootstrap = importlib.import_module("edge.runtime.health_bootstrap")
        except ModuleNotFoundError:
            bootstrap = None
        if bootstrap is not None:
            stack.enter_context(mock.patch.object(bootstrap, "_exit_process", exit_calls.append))

        from edge.config import load_config
        from edge.main import run_daemon

        run_daemon(load_config())

    config = _describe_config(recorder.client_config)
    return {
        "routes": config["routes"],
        "mqtt": config["mqtt"],
        "publishes": _normalize(recorder.publishes),
        "model_calls": recorder.model_calls,
        "_exit_calls": exit_calls,
        "_keepalive": getattr(getattr(recorder.client_config, "mqtt", None), "keepalive", None),
    }


def comparable(result: dict[str, Any]) -> dict[str, Any]:
    """去掉不列入比對的欄位（以底線開頭）。"""
    return {key: value for key, value in result.items() if not key.startswith("_")}


def load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["result"]
