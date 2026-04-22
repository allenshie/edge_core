"""Pipeline-level health summary formatting helpers."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


_SUMMARY_COLUMNS: list[tuple[str, str]] = [
    ("stage", "stage"),
    ("state", "state"),
    ("session_id", "session_id"),
    ("frame_seq", "frame_seq"),
    ("capture_fps", "capture_fps"),
    ("infer_fps", "infer_fps"),
    ("stream_output_fps", "stream_output_fps"),
    ("stream_unique_fps", "stream_unique_fps"),
    ("age_s", "age_s"),
    ("alive", "alive"),
    ("note", "note"),
]

_STATE_ORDER = {
    "error": 0,
    "stalled": 1,
    "degraded": 2,
    "ok": 3,
    "disabled": 4,
    "inactive": 5,
    None: 6,
}

_STAGE_ORDER = {
    "pipeline": -1,
    "ingest": 0,
    "infer": 1,
    "stream": 2,
    "publish": 3,
}


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_pipeline_summary(rows: Sequence[Mapping[str, Any]]) -> str:
    stage_rows = _normalize_rows(rows)
    if not stage_rows:
        return "pipeline summary\n(no health snapshots)"

    aggregate_row = _build_aggregate_row(stage_rows)
    all_rows = [aggregate_row, *stage_rows]

    widths: dict[str, int] = {}
    for key, header in _SUMMARY_COLUMNS:
        widths[key] = len(header)
    for row in all_rows:
        for key, _ in _SUMMARY_COLUMNS:
            widths[key] = max(widths[key], len(_format_value(row.get(key))))

    header = " | ".join(header.ljust(widths[key]) for key, header in _SUMMARY_COLUMNS)
    lines = ["pipeline summary", header]
    for row in all_rows:
        rendered = " | ".join(_format_value(row.get(key)).ljust(widths[key]) for key, _ in _SUMMARY_COLUMNS)
        lines.append(rendered)
    return "\n".join(lines)


def _normalize_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        copy = dict(row)
        stage = str(copy.get("stage") or copy.get("task") or "").strip().lower()
        copy["stage"] = stage or "unknown"
        normalized.append(copy)
    normalized.sort(key=lambda row: _STAGE_ORDER.get(row.get("stage"), 99))
    return normalized


def _build_aggregate_row(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    state = _aggregate_state(rows)
    session_id = next((row.get("session_id") for row in rows if row.get("session_id")), None)
    frame_seq = max((int(row["frame_seq"]) for row in rows if isinstance(row.get("frame_seq"), int)), default=None)
    age_s = max(
        (float(row["age_s"]) for row in rows if isinstance(row.get("age_s"), (int, float))),
        default=None,
    )
    alive = all(bool(row.get("alive", True)) for row in rows)
    bottleneck = _pick_bottleneck(rows)
    capture_fps = _find_metric(rows, "capture_fps", "ingest")
    infer_fps = _find_metric(rows, "infer_fps", "infer")
    stream_output_fps = _find_metric(rows, "stream_output_fps", "stream")
    stream_unique_fps = _find_metric(rows, "stream_unique_fps", "stream")

    return {
        "stage": "pipeline",
        "state": state,
        "session_id": session_id,
        "frame_seq": frame_seq,
        "capture_fps": capture_fps,
        "infer_fps": infer_fps,
        "stream_output_fps": stream_output_fps,
        "stream_unique_fps": stream_unique_fps,
        "age_s": age_s,
        "alive": alive,
        "note": f"bottleneck={bottleneck}",
    }


def _aggregate_state(rows: Sequence[Mapping[str, Any]]) -> str:
    state = "ok"
    for row in rows:
        row_state = row.get("state")
        if _STATE_ORDER.get(str(row_state), 99) < _STATE_ORDER.get(state, 99):
            state = str(row_state)
    return state


def _pick_bottleneck(rows: Sequence[Mapping[str, Any]]) -> str:
    candidates: list[tuple[float, str]] = []
    for row in rows:
        stage = str(row.get("stage") or "unknown")
        age_s = row.get("age_s")
        if isinstance(age_s, (int, float)):
            candidates.append((float(age_s), stage))
    if not candidates:
        return "unknown"
    candidates.sort(reverse=True)
    return candidates[0][1]


def _find_metric(rows: Sequence[Mapping[str, Any]], metric_key: str, stage_name: str) -> Any:
    for row in rows:
        if str(row.get("stage")) == stage_name and row.get(metric_key) is not None:
            return row.get(metric_key)
    for row in rows:
        if row.get(metric_key) is not None:
            return row.get(metric_key)
    return None
