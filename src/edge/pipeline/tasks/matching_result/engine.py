"""Matching result receiver engine."""
from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from collections.abc import Mapping
from typing import Any

from smart_workflow import TaskContext

from edge.messaging import MATCHING_BROADCAST_ROUTE, MESSAGING_CLIENT_RESOURCE
from edge.runtime.shutdown_summary import cleanup_record
from edge.schema import MatchingResultPayload

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MatchingResultState:
    """Latest matching result snapshot cached in memory."""

    enabled: bool
    subscribed: bool
    camera_id: str
    schema_version: int | None = None
    message_type: str | None = None
    generated_at: str | None = None
    result_version: int = 0
    camera_matches: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    local_to_global: dict[int, Any] = field(default_factory=dict)
    payload: dict[str, Any] | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseMatchingResultEngine(ABC):
    """Base interface for matching result receiver engines."""

    def __init__(self, context: TaskContext | None = None) -> None:
        self._context = context

    @abstractmethod
    def snapshot(self) -> MatchingResultState:
        """Return the latest in-memory snapshot."""

    @abstractmethod
    def close(self) -> list[dict[str, object]]:
        """Release resources held by the engine."""


class DefaultMatchingResultEngine(BaseMatchingResultEngine):
    """Subscribe to matching broadcast and keep the latest camera-local table."""

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        matching_cfg = getattr(context.config, "matching_result", None) if context is not None else None
        camera_cfg = getattr(context.config, "camera", None) if context is not None else None
        self._enabled = bool(getattr(matching_cfg, "enabled", False)) if matching_cfg is not None else False
        self._camera_id = str(getattr(camera_cfg, "camera_id", "unknown") or "unknown")
        self._lock = threading.Lock()
        self._subscription_started = False
        self._result_version = 0
        self._state = MatchingResultState(
            enabled=self._enabled,
            subscribed=False,
            camera_id=self._camera_id,
            reason="matching_result_disabled" if not self._enabled else None,
        )
        self._client = self._resolve_client(context) if self._enabled and context is not None else None
        if self._enabled:
            self._subscribe()

    def snapshot(self) -> MatchingResultState:
        with self._lock:
            return MatchingResultState(
                enabled=self._state.enabled,
                subscribed=self._state.subscribed,
                camera_id=self._state.camera_id,
                schema_version=self._state.schema_version,
                message_type=self._state.message_type,
                generated_at=self._state.generated_at,
                result_version=self._state.result_version,
                camera_matches={camera_id: [dict(track) for track in tracks] for camera_id, tracks in self._state.camera_matches.items()},
                local_to_global=dict(self._state.local_to_global),
                payload=dict(self._state.payload) if self._state.payload is not None else None,
                reason=self._state.reason,
            )

    def close(self) -> list[dict[str, object]]:
        if not self._enabled:
            return [
                cleanup_record(
                    item="matching.result.subscription",
                    type="resource",
                    state="skipped",
                    ok=True,
                    alive_before=False,
                    alive_after=False,
                    detail="matching result disabled",
                )
            ]
        return [
            cleanup_record(
                item="matching.result.subscription",
                type="resource",
                state="skipped",
                ok=True,
                alive_before=self._subscription_started,
                alive_after=self._subscription_started,
                detail="shared messaging client retained",
            )
        ]

    def _resolve_client(self, context: TaskContext) -> Any | None:
        client = context.get_resource(MESSAGING_CLIENT_RESOURCE)
        if client is None:
            LOGGER.warning("matching result receiver skipped: messaging_client not ready")
        return client

    def _subscribe(self) -> None:
        if self._client is None:
            with self._lock:
                self._state.reason = "messaging_client_not_ready"
            return
        try:
            self._client.subscribe(MATCHING_BROADCAST_ROUTE, self._on_matching_result)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("matching result subscribe failed: %s", exc)
            with self._lock:
                self._state.reason = "subscribe_failed"
            return
        self._subscription_started = True
        with self._lock:
            self._state.subscribed = True
            self._state.reason = None
        LOGGER.info(
            "matching result receiver subscribed: route=%s camera=%s",
            MATCHING_BROADCAST_ROUTE,
            self._camera_id,
        )

    def _on_matching_result(self, payload: Mapping[str, Any]) -> None:
        try:
            message = MatchingResultPayload.from_dict(payload)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("matching result payload parse failed: %s", exc)
            with self._lock:
                self._state.reason = "payload_parse_failed"
            return

        camera_matches = {
            camera_id: [track.to_dict() for track in tracks]
            for camera_id, tracks in message.camera_matches.items()
        }
        local_to_global = message.local_to_global_mapping(self._camera_id)

        with self._lock:
            self._result_version += 1
            self._state = MatchingResultState(
                enabled=self._enabled,
                subscribed=self._subscription_started,
                camera_id=self._camera_id,
                schema_version=message.schema_version,
                message_type=message.message_type,
                generated_at=message.generated_at,
                result_version=self._result_version,
                camera_matches=camera_matches,
                local_to_global=local_to_global,
                payload=message.to_dict(),
                reason=None,
            )
