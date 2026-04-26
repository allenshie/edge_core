"""Structured stage logging helpers."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from edge.runtime.duration_meter import DurationMeter
from edge.schema import StageStats
from edge.runtime.rate_meter import RateMeter


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def build_stage_summary(
    stats: StageStats,
    extra_fields: Mapping[str, Any] | None = None,
) -> str:
    fields: list[tuple[str, Any]] = [
        ("task", stats.task_name),
        ("state", stats.health_state),
        ("session_id", stats.session_id),
        ("frame_seq", stats.last_frame_seq),
        ("capture_age_s", stats.capture_age_seconds()),
        ("age_s", stats.last_success_age_seconds()),
        ("queue_size", stats.queue_size),
        ("worker_alive", stats.worker_alive),
        ("warn", stats.warning_count),
        ("err", stats.error_count),
        ("latency_ms", stats.last_latency_ms),
    ]

    if stats.health_state == "degraded" and stats.last_warning_reason and (
        extra_fields is None or "reason" not in extra_fields
    ):
        fields.append(("reason", stats.last_warning_reason))
    if stats.health_state == "error" and stats.last_error_reason and (
        extra_fields is None or "reason" not in extra_fields
    ):
        fields.append(("reason", stats.last_error_reason))

    if extra_fields:
        fields.extend(extra_fields.items())

    rendered = []
    for key, value in fields:
        if value is None:
            continue
        rendered.append(f"{key}={_format_value(value)}")
    return " | ".join(rendered)


def emit_stage_summary(
    logger: logging.Logger,
    stats: StageStats,
    report_interval_seconds: float,
    *,
    extra_fields: Mapping[str, Any] | None = None,
    force: bool = False,
    level: int = logging.INFO,
) -> str | None:
    if not force and not stats.should_report(report_interval_seconds):
        return None
    line = build_stage_summary(stats, extra_fields)
    logger.log(level, line)
    stats.mark_reported()
    return line


def emit_task_summary(
    logger: logging.Logger,
    stats: StageStats,
    report_interval_seconds: float,
    *,
    extra_fields: Mapping[str, Any] | None = None,
    force: bool = False,
    level: int = logging.INFO,
    monitor: Any | None = None,
    event_type: str | None = None,
    component: str | None = None,
) -> str | None:
    line = emit_stage_summary(
        logger,
        stats,
        report_interval_seconds,
        extra_fields=extra_fields,
        force=force,
        level=level,
    )
    if line is None:
        return None
    if monitor is not None and event_type is not None:
        monitor.report_event(event_type, detail=line, component=component or stats.task_name)
    return line


def emit_task_health(
    context: Any,
    stats: StageStats,
    *,
    health_state: str,
    extra_fields: Mapping[str, Any] | None = None,
    reason: str | None = None,
    worker_alive: bool | None = None,
    queue_size: int | None = None,
    event_type: str | None = None,
    report_interval_seconds: float | None = None,
    level: int | None = None,
    force: bool = False,
    rate_meter: RateMeter | DurationMeter | None = None,
    rate_prefix: str | None = None,
) -> str | None:
    if report_interval_seconds is None:
        report_interval_seconds = float(
            getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0
        )
    should_emit = force or stats.should_report(report_interval_seconds) or stats.last_reported_state != health_state
    if not should_emit:
        return None

    if health_state == "error":
        stats.record_error(reason, worker_alive=worker_alive, queue_size=queue_size)
    elif health_state in {"degraded", "stalled"}:
        stats.record_warning(reason, worker_alive=worker_alive, queue_size=queue_size)

    stats.health_state = health_state
    merged_fields: dict[str, Any] | None = dict(extra_fields) if extra_fields else None
    if rate_meter is not None and rate_prefix:
        rate_fields = rate_meter.snapshot(rate_prefix)
        merged_fields = merged_fields or {}
        merged_fields.update(rate_fields)
    line = emit_stage_summary(
        context.logger,
        stats,
        report_interval_seconds,
        extra_fields=merged_fields,
        force=True,
        level=level
        if level is not None
        else logging.WARNING
        if health_state in {"degraded", "stalled", "error"}
        else logging.INFO,
    )
    if line is None:
        return None
    if rate_meter is not None and rate_prefix:
        rate_meter.mark_reported()
    if event_type is not None:
        context.monitor.report_event(event_type, detail=line, component=stats.task_name)
    return line
