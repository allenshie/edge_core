from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


def _default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def get_root() -> Path:
    root = os.environ.get("SMART_WAREHOUSE_ROOT")
    if root:
        return Path(root).expanduser().resolve()
    return _default_root()


def get_registry_path() -> Path:
    return get_root() / "config" / "cameras.yaml"


def resolve_from_root(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return (get_root() / path).resolve()


def load_registry() -> Dict[str, Any]:
    path = get_registry_path()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("cameras", {}) or {}


def get_camera_entry(camera_id: str) -> Dict[str, Any] | None:
    cameras = load_registry()
    direct = cameras.get(camera_id)
    if direct:
        return direct
    for _, entry in cameras.items():
        aliases = entry.get("aliases") or []
        if camera_id in aliases:
            return entry
    return None
