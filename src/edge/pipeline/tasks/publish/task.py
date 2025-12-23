"""發布推理結果至整合端。"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from smart_workflow import BaseTask, TaskContext, TaskResult

from edge.schema import EdgeEvent

LOGGER = logging.getLogger(__name__)


class PublishResultTask(BaseTask):
    name = "edge-publish"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._integration_config = context.config.integration if context else None
        self._camera_id = context.config.camera.camera_id if context else None

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        detections = context.get_resource("inference_output") or []
        camera_id = self._camera_id or context.config.camera.camera_id
        event = EdgeEvent.now(camera_id=camera_id, detections=detections)

        payload = json.dumps(event.to_dict()).encode("utf-8")
        integration = self._integration_config or context.config.integration
        url = f"{integration.api_base}/edge/events"
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=integration.timeout_seconds) as resp:
                status = resp.status
        except urllib.error.URLError as exc:
            LOGGER.warning("無法呼叫整合端 API (%s)：%s", url, exc)
            status = None
        context.monitor.report_event(
            "edge_publish",
            detail=f"detections={len(detections)} status={status}",
            component=self.name,
        )
        return TaskResult(payload={"published": len(detections), "status": status})
