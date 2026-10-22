"""Health server lifecycle helpers."""
from __future__ import annotations

import os

from smart_workflow import HealthServer, HealthState, ProbeConfig

from edge.runtime.shutdown_summary import cleanup_record


class EdgeHealthState(HealthState):
    """Track core runtime health independently from optional streaming output."""

    def __init__(self, logger) -> None:
        super().__init__()
        _ = logger
        self._fatal_reason: str | None = None

    @property
    def fatal_reason(self) -> str | None:
        return self._fatal_reason

    def mark_fatal(self, reason: str) -> None:
        """標記無法自行恢復的錯誤：之後不再更新 loop / progress，讓 liveness 與 readiness 逾時失敗。"""
        self._fatal_reason = reason
        super().mark_error(reason)

    def mark_loop_tick(self) -> None:
        if self._fatal_reason is None:
            super().mark_loop_tick()

    def mark_progress(self) -> None:
        if self._fatal_reason is None:
            super().mark_progress()


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
