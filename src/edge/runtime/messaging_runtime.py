"""Messaging lifecycle helpers for edge runtime."""
from __future__ import annotations

import os

from edge.messaging import (
    MATCHING_BROADCAST_ROUTE,
    MESSAGING_CLIENT_RESOURCE,
    PHASE_UPDATES_ROUTE,
    MessagingClientProvider,
    resolve_app_inbound_backend,
    resolve_events_route,
    resolve_inbound_route_specs,
)
from edge.runtime.shutdown_summary import cleanup_record
from edge.schema import MatchingResultPayload


def init_messaging_client(context, logger):
    config = context.config
    messaging = MessagingClientProvider(config).build()
    context.set_resource(MESSAGING_CLIENT_RESOURCE, messaging)

    events_route = resolve_events_route(config)
    inbound_backend = resolve_app_inbound_backend(config)
    phase_route, matching_route = resolve_inbound_route_specs(config)
    logger.info(
        "messaging client ready (edge_events=%s, inbound_backend=%s, phase_updates=%s, matching_result=%s)",
        events_route[0] if events_route else "none",
        inbound_backend,
        phase_route.channel if phase_route.enabled else "disabled",
        matching_route.channel if matching_route.enabled else "disabled",
    )
    return messaging


def start_messaging_subscriber(context) -> None:
    config = context.config
    inbound_specs = [spec for spec in resolve_inbound_route_specs(config) if spec.enabled]
    if not inbound_specs:
        context.logger.info("inbound subscribers skipped: no enabled routes")
        return

    messaging = context.get_resource(MESSAGING_CLIENT_RESOURCE)
    if messaging is None:
        context.logger.warning("inbound subscribers skipped: messaging_client not ready")
        return

    subscribed_routes: list[str] = []

    for spec in inbound_specs:
        if spec.route_key == PHASE_UPDATES_ROUTE:
            if _subscribe_phase_route(context, messaging, spec):
                subscribed_routes.append(spec.route_key)
            continue
        if spec.route_key == MATCHING_BROADCAST_ROUTE:
            if _subscribe_matching_route(context, messaging, spec):
                subscribed_routes.append(spec.route_key)
            continue
        context.logger.warning("inbound subscriber skipped: unsupported route=%s", spec.route_key)

    if not subscribed_routes:
        context.logger.info("inbound subscribers disabled after scan: no route subscribed")
        return

    context.logger.info("inbound subscribers ready: %s", ", ".join(subscribed_routes))


def _subscribe_phase_route(context, messaging, spec) -> bool:
    def _on_phase(payload: dict) -> None:
        if os.environ.get("EDGE_MODE_STRATEGY", "external").lower() != "external":
            return
        mode = str(payload.get("phase") or payload.get("mode") or "").strip().lower()
        if not mode:
            return
        context.set_resource(spec.resource_name, mode)
        context.logger.info("Messaging mode update: %s", mode)

    try:
        messaging.subscribe(spec.route_key, _on_phase)
    except Exception as exc:  # pylint: disable=broad-except
        context.logger.warning("phase subscribe failed; continue without route: %s", exc)
        return False

    context.logger.info("phase subscriber ready (route=%s resource=%s)", spec.route_key, spec.resource_name)
    return True


def _subscribe_matching_route(context, messaging, spec) -> bool:
    result_version = 0
    camera_id = str(getattr(getattr(context.config, "camera", None), "camera_id", "unknown") or "unknown")

    def _on_matching(payload: dict) -> None:
        nonlocal result_version
        try:
            message = MatchingResultPayload.from_dict(payload)
        except Exception as exc:  # noqa: BLE001
            context.logger.warning("matching result payload parse failed: %s", exc)
            return

        result_version += 1
        snapshot = message.to_local_snapshot(
            camera_id,
            result_version=result_version,
            enabled=True,
            subscribed=True,
            reason=None,
        )
        context.set_resource(spec.resource_name, snapshot)
        context.logger.info(
            "Matching result update: camera=%s matches=%s version=%s",
            camera_id,
            snapshot["matches"],
            result_version,
        )

    try:
        messaging.subscribe(spec.route_key, _on_matching)
    except Exception as exc:  # noqa: BLE001
        context.logger.warning("matching subscribe failed; continue without route: %s", exc)
        return False

    context.logger.info("matching subscriber ready (route=%s resource=%s)", spec.route_key, spec.resource_name)
    return True


def close_messaging_client(context) -> list[dict]:
    messaging = context.get_resource(MESSAGING_CLIENT_RESOURCE)
    if messaging is None:
        return [
            cleanup_record(
                item="runtime.messaging_client",
                type="resource",
                state="skipped",
                ok=True,
                alive_before=False,
                alive_after=False,
                detail="messaging client not initialized",
            )
        ]
    try:
        messaging.close()
    except Exception as exc:  # noqa: BLE001
        return [
            cleanup_record(
                item="runtime.messaging_client",
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
            item="runtime.messaging_client",
            type="resource",
            state="done",
            ok=True,
            alive_before=True,
            alive_after=False,
            detail="messaging client closed",
        )
    ]
