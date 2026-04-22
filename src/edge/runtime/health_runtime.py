"""Health server lifecycle helpers."""
from __future__ import annotations

import os

from smart_workflow import HealthServer, HealthState, ProbeConfig

from edge.runtime.shutdown_summary import cleanup_record


def is_health_enabled() -> bool:
    value = os.getenv("EDGE_HEALTH_SERVER_ENABLED")
    if value is None:
        return False
    return value.strip().lower() not in {"0", "false", "no", "off"}


def start_health_server(context, logger):
    if not is_health_enabled():
        return None, None

    health_state = HealthState()
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
