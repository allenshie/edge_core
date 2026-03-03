"""Schedule loading helpers for scheduled inference engine."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from smart_workflow import TaskError

LOGGER = logging.getLogger(__name__)


def resolve_resource_root() -> Path:
    root = os.environ.get("EDGE_RESOURCE_ROOT")
    if not root:
        return Path.cwd()
    candidate = Path(root).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    return candidate


def get_schedule_path(resource_root: Path, default_schedule: Path) -> Path:
    env_path = os.environ.get("EDGE_SCHEDULE_PATH") or os.environ.get("EDGE_DEMO_SCHEDULE_PATH")
    if env_path:
        candidate = Path(env_path).expanduser()
        if not candidate.is_absolute():
            candidate = (resource_root / env_path).resolve()
        return candidate
    return default_schedule


def extract_phase_entries(definition: Any) -> List[Dict[str, Any]]:
    if isinstance(definition, list):
        return definition
    if isinstance(definition, dict):
        candidates = [
            definition.get("tasks"),
            definition.get("inference"),
            definition.get("inference_tasks"),
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return candidate
    raise TaskError("schedule phase 定義必須是 list 或包含 tasks/inference 的 object")


def load_schedule_json(schedule_path: Path) -> Dict[str, Any]:
    if not schedule_path.exists():
        raise TaskError(f"找不到排程設定檔：{schedule_path}")
    try:
        return json.loads(schedule_path.read_text())
    except json.JSONDecodeError as exc:
        raise TaskError(f"排程設定檔格式錯誤：{exc}") from exc


def load_streaming_phase_policy(resource_root: Path, default_schedule: Path) -> Dict[str, bool]:
    schedule_path = get_schedule_path(resource_root, default_schedule)
    if not schedule_path.exists():
        return {}
    try:
        data = json.loads(schedule_path.read_text())
    except json.JSONDecodeError:
        LOGGER.warning("invalid schedule json, skip streaming policy: %s", schedule_path)
        return {}

    policy: Dict[str, bool] = {}
    for phase, definition in data.items():
        if isinstance(definition, dict):
            streaming = definition.get("streaming", {})
            if isinstance(streaming, dict) and "enabled" in streaming:
                policy[phase] = bool(streaming["enabled"])
    if policy:
        LOGGER.info("loaded streaming phase policy from %s: %s", schedule_path, policy)
    return policy


__all__ = [
    "resolve_resource_root",
    "get_schedule_path",
    "extract_phase_entries",
    "load_schedule_json",
    "load_streaming_phase_policy",
]
