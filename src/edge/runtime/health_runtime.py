"""Health server lifecycle helpers."""
from __future__ import annotations

import os
import threading
from dataclasses import replace

from smart_workflow import HealthServer, HealthState, ProbeConfig

from edge.runtime.shutdown_summary import cleanup_record


class EdgeHealthState(HealthState):
    """Keep control-loop liveness independent from phase-aware readiness."""

    def __init__(self, logger) -> None:
        super().__init__()
        self._logger = logger
        self._readiness_lock = threading.Lock()
        self._service_ready = True
        self._readiness_phase = "startup"
        self._readiness_reason = "startup"

    def set_service_ready(self, ready: bool, *, phase: str, reason: str) -> None:
        with self._readiness_lock:
            changed = (
                self._service_ready != ready
                or self._readiness_phase != phase
                or self._readiness_reason != reason
            )
            self._service_ready = ready
            self._readiness_phase = phase
            self._readiness_reason = reason
        if changed:
            self._logger.info(
                "service readiness changed: ready=%s phase=%s reason=%s",
                ready,
                phase,
                reason,
            )

    def snapshot(self):
        snapshot = super().snapshot()
        with self._readiness_lock:
            service_ready = self._service_ready
        if service_ready:
            return snapshot
        # smart-workflow currently exposes one readiness-only gate (`in_backoff`).
        # Overlay the desired service state there; liveness does not inspect it.
        return replace(snapshot, in_backoff=True)


def is_health_enabled() -> bool:
    value = os.getenv("EDGE_HEALTH_SERVER_ENABLED")
    if value is None:
        return False
    return value.strip().lower() not in {"0", "false", "no", "off"}


def start_health_server(context, logger):
    if not is_health_enabled():
        return None, None

    health_state = EdgeHealthState(logger)
    context.set_resource("health_state", health_state)

    host = os.environ.get("EDGE_HEALTH_SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("EDGE_HEALTH_SERVER_PORT", "8081"))

    server = HealthServer(
        health_state=health_state,
        host=host,
        port=port,
        probe_config=ProbeConfig(
            liveness_timeout_seconds=float(
                os.environ.get("EDGE_HEALTH_LIVENESS_TIMEOUT_SECONDS", "30")
            ),
            readiness_timeout_seconds=float(
                os.environ.get("EDGE_HEALTH_READINESS_TIMEOUT_SECONDS", "30")
            ),
            startup_grace_seconds=float(
                os.environ.get("EDGE_HEALTH_STARTUP_GRACE_SECONDS", "10")
            ),
        ),
    )
    server.start()
    logger.info("health server started at %s:%s", host, port)
    return server, health_state


def stop_health_server(server) -> list[dict]:
    if server is None:
        return [
            cleanup_record(
                item="health.server",
                type="server",
                state="skipped",
                ok=True,
                alive_before=False,
                alive_after=False,
                detail="health server disabled",
            )
        ]
    try:
        server.stop()
    except Exception as exc:  # noqa: BLE001
        return [
            cleanup_record(
                item="health.server",
                type="server",
                state="failed",
                ok=False,
                alive_before=True,
                alive_after=True,
                detail="health server stop failed",
                error=str(exc),
            )
        ]
    return [
        cleanup_record(
            item="health.server",
            type="server",
            state="done",
            ok=True,
            alive_before=True,
            alive_after=False,
            detail="health server stopped",
        )
    ]
