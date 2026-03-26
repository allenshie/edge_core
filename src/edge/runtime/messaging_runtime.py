"""Messaging lifecycle helpers for edge runtime."""
from __future__ import annotations

import os
from contextlib import suppress

from edge.api.mode_server import MODE_RESOURCE
from edge.messaging import (
    MESSAGING_CLIENT_RESOURCE,
    MessagingClientProvider,
    PHASE_UPDATES_ROUTE,
    resolve_events_route,
    resolve_phase_updates_route,
)


def init_messaging_client(context, logger):
    config = context.config
    messaging = MessagingClientProvider(config).build()
    context.set_resource(MESSAGING_CLIENT_RESOURCE, messaging)

    events_route = resolve_events_route(config)
    phase_route = resolve_phase_updates_route(config)
    logger.info(
        "messaging client ready (edge_events=%s, phase_updates=%s)",
        events_route[0] if events_route else "none",
        phase_route[0] if phase_route else "none",
    )
    return messaging


def start_messaging_subscriber(context) -> None:
    config = context.config
    phase_route = resolve_phase_updates_route(config)
    if phase_route is None:
        return

    messaging = context.get_resource(MESSAGING_CLIENT_RESOURCE)
    if messaging is None:
        context.logger.warning("phase subscriber skipped: messaging_client not ready")
        return

    def _on_phase(payload: dict) -> None:
        if os.environ.get("EDGE_MODE_STRATEGY", "external").lower() != "external":
            return
        mode = (payload.get("phase") or payload.get("mode") or "").strip().lower()
        if not mode:
            return
        context.set_resource(MODE_RESOURCE, mode)
        context.logger.info("Messaging mode update: %s", mode)

    try:
        messaging.subscribe(PHASE_UPDATES_ROUTE, _on_phase)
    except Exception as exc:  # pylint: disable=broad-except
        context.logger.warning("phase subscribe failed; continue without route: %s", exc)
        return

    context.logger.info("phase subscriber ready (backend=%s route=%s)", phase_route[0], PHASE_UPDATES_ROUTE)


def close_messaging_client(context) -> None:
    messaging = context.get_resource(MESSAGING_CLIENT_RESOURCE)
    if messaging is None:
        return
    with suppress(Exception):
        messaging.close()
