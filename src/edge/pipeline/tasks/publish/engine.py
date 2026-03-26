"""Engine 實作：負責將推理結果傳遞到整合端。"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Sequence

from smart_messaging_core import MessagingClient
from smart_workflow import TaskContext

from edge.messaging import EDGE_EVENTS_ROUTE, MESSAGING_CLIENT_RESOURCE, MessagingClientProvider
from edge.schema import EdgeDetection, EdgeEvent

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
        self._client, self._is_shared_client = self._resolve_client(context)

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
        ok = self._client.publish(EDGE_EVENTS_ROUTE, payload)
        status = 200 if ok else None
        return PublishOutcome(published=len(detections), status=status)

    def _resolve_client(self, context: TaskContext | None) -> tuple[MessagingClient, bool]:
        if context is not None:
            client = context.get_resource(MESSAGING_CLIENT_RESOURCE)
            if isinstance(client, MessagingClient):
                return client, True

            provider = MessagingClientProvider(context.config)
            created_client = provider.build()
            context.set_resource(MESSAGING_CLIENT_RESOURCE, created_client)
            return created_client, False

        raise ValueError("MessagingPublishEngine requires TaskContext to initialize messaging client")

    def close(self) -> None:
        if self._is_shared_client:
            return
        close_fn = getattr(self._client, "close", None)
        if callable(close_fn):
            close_fn()
