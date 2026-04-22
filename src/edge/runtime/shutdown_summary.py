"""Shutdown / cleanup summary helpers."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

SHUTDOWN_CLEANUP_RECORDS_RESOURCE = "shutdown_cleanup_records"

_SUMMARY_COLUMNS: list[tuple[str, str]] = [
    ("item", "item"),
    ("type", "type"),
    ("state", "state"),
    ("ok", "ok"),
    ("alive_before", "alive_before"),
    ("alive_after", "alive_after"),
    ("duration_ms", "duration_ms"),
    ("detail", "detail"),
    ("error", "error"),
]


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def cleanup_record(
    *,
    item: str,
    type: str,
    state: str,
    ok: bool,
    alive_before: bool | None = None,
    alive_after: bool | None = None,
    duration_ms: float | None = None,
    detail: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "item": item,
        "type": type,
        "state": state,
        "ok": ok,
        "alive_before": alive_before,
        "alive_after": alive_after,
        "duration_ms": duration_ms,
        "detail": detail,
        "error": error,
    }


def normalize_cleanup_records(
    result: Any,
    *,
    fallback_item: str | None = None,
    fallback_type: str = "resource",
) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, Mapping):
        record = dict(result)
        if fallback_item and not record.get("item"):
            record["item"] = fallback_item
        if not record.get("type"):
            record["type"] = fallback_type
        return [record]
    if isinstance(result, (list, tuple)):
        records: list[dict[str, Any]] = []
        for item in result:
            records.extend(
                normalize_cleanup_records(
                    item,
                    fallback_item=fallback_item,
                    fallback_type=fallback_type,
                )
            )
        return records
    if fallback_item is None:
        fallback_item = "cleanup"
    return [
        cleanup_record(
            item=fallback_item,
            type=fallback_type,
            state="done",
            ok=True,
            detail=str(result),
        )
    ]


def append_shutdown_records(context: Any, records: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    normalized = normalize_cleanup_records(records)
    if not normalized:
        return []
    current = context.get_resource(SHUTDOWN_CLEANUP_RECORDS_RESOURCE)
    if not isinstance(current, list):
        current = []
    current.extend(normalized)
    context.set_resource(SHUTDOWN_CLEANUP_RECORDS_RESOURCE, current)
    return normalized


def get_shutdown_records(context: Any) -> list[dict[str, Any]]:
    current = context.get_resource(SHUTDOWN_CLEANUP_RECORDS_RESOURCE)
    if not isinstance(current, list):
        return []
    return [dict(item) for item in current if isinstance(item, Mapping)]


def build_shutdown_summary(records: Sequence[Mapping[str, Any]]) -> str:
    normalized = [dict(record) for record in records if record]
    if not normalized:
        return "shutdown summary\n(no cleanup records)"

    widths: dict[str, int] = {key: len(header) for key, header in _SUMMARY_COLUMNS}
    for row in normalized:
        for key, _ in _SUMMARY_COLUMNS:
            widths[key] = max(widths[key], len(_format_value(row.get(key))))

    header = " | ".join(header.ljust(widths[key]) for key, header in _SUMMARY_COLUMNS)
    lines = ["shutdown summary", header]
    for row in normalized:
        rendered = " | ".join(_format_value(row.get(key)).ljust(widths[key]) for key, _ in _SUMMARY_COLUMNS)
        lines.append(rendered)

    ok_count = sum(1 for row in normalized if bool(row.get("ok")))
    failed_count = sum(1 for row in normalized if str(row.get("state")) == "failed")
    timeout_count = sum(1 for row in normalized if str(row.get("state")) == "timeout")
    alive_after_count = sum(1 for row in normalized if bool(row.get("alive_after")))
    lines.append(
        "shutdown result | ok=%s | items=%d | failed=%d | timeout=%d | alive_after=%d"
        % (ok_count == len(normalized), len(normalized), failed_count, timeout_count, alive_after_count)
    )
    return "\n".join(lines)


def emit_shutdown_summary(logger: logging.Logger, records: Sequence[Mapping[str, Any]]) -> str | None:
    normalized = [dict(record) for record in records if record]
    if not normalized:
        return None
    summary = build_shutdown_summary(normalized)
    logger.info(summary)
    return summary
