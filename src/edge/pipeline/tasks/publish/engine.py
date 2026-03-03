"""Engine 實作：負責將推理結果傳遞到整合端。"""
from __future__ import annotations
import os
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Sequence

from smart_workflow import TaskContext

from edge.schema import EdgeDetection, EdgeEvent

from smart_messaging_core import HttpConfig, MessagingClient, MessagingConfig, MqttConfig

LOGGER = logging.getLogger(__name__)


@dataclass
class PublishOutcome:
    """標準化的發布結果。"""

    published: int
    status: int | None


class BasePublishEngine:
    """自訂 PublishResultTask 行為時可繼承的基底引擎。"""

    def __init__(self, context: TaskContext | None = None) -> None:
        self._integration_config = context.config.integration if context else None
        self._camera_config = context.config.camera if context else None

    def publish(self, context: TaskContext, detections: Sequence[EdgeDetection]) -> PublishOutcome:
        raise NotImplementedError


class DefaultPublishEngine(BasePublishEngine):
    """預設行為：將事件送至整合端 REST API。"""

    def publish(self, context: TaskContext, detections: Sequence[EdgeDetection]) -> PublishOutcome:
        camera_id = self._camera_config.camera_id if self._camera_config else context.config.camera.camera_id
        integration = self._integration_config or context.config.integration

        models_run = context.get_resource("inference_models_run") or []
        models_reuse = context.get_resource("inference_models_reuse") or []
        models = list(models_run)
        for name in models_reuse:
            if name not in models:
                models.append(name)
        event = EdgeEvent.now(camera_id=camera_id, detections=list(detections), models=models)
        payload = json.dumps(event.to_dict()).encode("utf-8")
        url = f"{integration.api_base}/edge/events"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

        status: int | None
        try:
            with urllib.request.urlopen(req, timeout=integration.timeout_seconds) as resp:
                status = resp.status
        except urllib.error.URLError as exc:
            LOGGER.warning("無法呼叫整合端 API (%s)：%s", url, exc)
            status = None
        return PublishOutcome(published=len(detections), status=status)


class MessagingPublishEngine(BasePublishEngine):
    """Publish engine using smart_messaging_core."""

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._client = self._build_client(context)
        self._topic = os.environ.get("EDGE_EVENTS_MQTT_TOPIC", "edge/events").strip() or "edge/events"
        self._backend = os.environ.get("EDGE_PUBLISH_BACKEND", "http").strip().lower()

    def publish(self, context: TaskContext, detections: Sequence[EdgeDetection]) -> PublishOutcome:
        camera_id = self._camera_config.camera_id if self._camera_config else context.config.camera.camera_id
        models_run = context.get_resource("inference_models_run") or []
        models_reuse = context.get_resource("inference_models_reuse") or []
        models = list(models_run)
        for name in models_reuse:
            if name not in models:
                models.append(name)
        event = EdgeEvent.now(camera_id=camera_id, detections=list(detections), models=models)
        payload = event.to_dict()
        ok = self._client.publish(self._topic, payload)
        status = 200 if ok else None
        return PublishOutcome(published=len(detections), status=status)

    def _build_client(self, context: TaskContext | None) -> MessagingClient:
        integration = self._integration_config or (context.config.integration if context else None)
        mqtt_cfg = getattr(context.config, "mqtt", None) if context else None
        mqtt = MqttConfig(
            host=mqtt_cfg.host if mqtt_cfg else os.environ.get("EDGE_MQTT_HOST", "localhost"),
            port=mqtt_cfg.port if mqtt_cfg else int(os.environ.get("EDGE_MQTT_PORT", "1883")),
            qos=mqtt_cfg.qos if mqtt_cfg else int(os.environ.get("EDGE_MQTT_QOS", "1")),
            retain=False,
            client_id=getattr(mqtt_cfg, "client_id", None) if mqtt_cfg else os.environ.get("EDGE_MQTT_CLIENT_ID"),
        )
        http = None
        if integration:
            http = HttpConfig(base_url=integration.api_base, timeout_seconds=integration.timeout_seconds)
        topic_routes = {"edge/events": "/edge/events"}
        cfg = MessagingConfig(
            publish_backend=os.environ.get("EDGE_PUBLISH_BACKEND", "http").strip().lower(),
            subscribe_backend="none",
            mqtt=mqtt,
            http=http,
            topic_routes=topic_routes,
        )
        return MessagingClient(cfg)
