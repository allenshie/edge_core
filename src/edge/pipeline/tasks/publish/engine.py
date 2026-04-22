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

from edge.messaging import EDGE_EVENTS_ROUTE, MessagingClientProvider
from edge.runtime.shutdown_summary import cleanup_record
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

    def publish(
        self,
        detections: Sequence[EdgeDetection],
        *,
        models_run: Sequence[str] | None = None,
        models_reuse: Sequence[str] | None = None,
    ) -> PublishOutcome:
        raise NotImplementedError

    def close(self) -> list[dict[str, object]]:
        return [
            cleanup_record(
                item="publish.engine",
                type="engine",
                state="done",
                ok=True,
                alive_before=False,
                alive_after=False,
                detail="no-op",
            )
        ]

    def _merge_models(
        self,
        models_run: Sequence[str] | None = None,
        models_reuse: Sequence[str] | None = None,
    ) -> list[str]:
        models = list(models_run or [])
        for name in models_reuse or []:
            if name not in models:
                models.append(name)
        return models


class DefaultPublishEngine(BasePublishEngine):
    """預設行為：將事件送至整合端 REST API。"""

    def publish(
        self,
        detections: Sequence[EdgeDetection],
        *,
        models_run: Sequence[str] | None = None,
        models_reuse: Sequence[str] | None = None,
    ) -> PublishOutcome:
        camera_id = self._camera_config.camera_id if self._camera_config else "unknown"
        integration = self._integration_config
        if integration is None:
            raise ValueError("DefaultPublishEngine requires integration config")
        models = self._merge_models(models_run, models_reuse)
        event = EdgeEvent.now(camera_id=camera_id, detections=list(detections), models=models)
        payload = json.dumps(event.to_dict()).encode("utf-8")
        url = f"{integration.api_base}/edge/events"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

        status: int | None
        try:
            with urllib.request.urlopen(req, timeout=integration.timeout_seconds) as resp:
                status = resp.status
        except urllib.error.URLError as exc:
            LOGGER.debug("無法呼叫整合端 API (%s)：%s", url, exc)
            status = None
        return PublishOutcome(published=len(detections), status=status)


class MessagingPublishEngine(BasePublishEngine):
    """Publish engine using smart_messaging_core."""

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._client, self._is_shared_client = self._resolve_client(context)

    def publish(
        self,
        detections: Sequence[EdgeDetection],
        *,
        models_run: Sequence[str] | None = None,
        models_reuse: Sequence[str] | None = None,
    ) -> PublishOutcome:
        camera_id = self._camera_config.camera_id if self._camera_config else "unknown"
        models = self._merge_models(models_run, models_reuse)
        event = EdgeEvent.now(camera_id=camera_id, detections=list(detections), models=models)
        payload = event.to_dict()
        ok = self._client.publish(EDGE_EVENTS_ROUTE, payload)
        status = 200 if ok else None
        return PublishOutcome(published=len(detections), status=status)

    def _resolve_client(self, context: TaskContext | None) -> tuple[MessagingClient, bool]:
        if context is not None:
            provider = MessagingClientProvider(context.config)
            created_client = provider.build()
            return created_client, False
        raise ValueError("MessagingPublishEngine requires TaskContext to initialize messaging client")

    def close(self) -> list[dict[str, object]]:
        if self._is_shared_client:
            return [
                cleanup_record(
                    item="publish.client",
                    type="resource",
                    state="skipped",
                    ok=True,
                    alive_before=False,
                    alive_after=False,
                    detail="shared client retained",
                )
            ]
        close_fn = getattr(self._client, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception as exc:  # noqa: BLE001
                return [
                    cleanup_record(
                        item="publish.client",
                        type="resource",
                        state="failed",
                        ok=False,
                        alive_before=True,
                        alive_after=True,
                        detail="messaging client close failed",
                        error=str(exc),
                    )
                ]
        return [
            cleanup_record(
                item="publish.client",
                type="resource",
                state="done",
                ok=True,
                alive_before=True,
                alive_after=False,
                detail="messaging client closed",
            )
        ]
