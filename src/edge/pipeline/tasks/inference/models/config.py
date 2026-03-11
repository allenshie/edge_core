from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def resolve_resource_root() -> Path:
    root = os.environ.get("EDGE_RESOURCE_ROOT")
    if not root:
        return Path.cwd()
    candidate = Path(root).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    return candidate


def resolve_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (resolve_resource_root() / raw_path).resolve()
    return path


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("PyYAML is required to load config files.") from exc
    return yaml.safe_load(path.read_text()) or None


def load_models_config() -> dict:
    env_path = os.environ.get("EDGE_MODELS_CONFIG")
    config_path = resolve_path(env_path) if env_path else resolve_path("configs/models.yaml")
    if not config_path:
        return {}
    data = load_yaml(config_path)
    return data if isinstance(data, dict) else {}


def get_model_config(model_name: str) -> dict:
    data = load_models_config()
    if not data:
        return {}
    return data.get(model_name, {}) if isinstance(data, dict) else {}
