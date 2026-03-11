from __future__ import annotations

import os
from abc import abstractmethod
from pathlib import Path
from typing import Any, List, Protocol

from edge.schema import EdgeDetection

from .base import BaseEdgeModel
from .config import load_yaml, resolve_path


class PathResolver(Protocol):
    def __call__(self, raw_path: str | None) -> Path | None: ...


class YamlLoader(Protocol):
    def __call__(self, path: Path) -> Any: ...


class BaseYamlMockModel(BaseEdgeModel):
    """Shared config-driven mock inference flow backed by YAML records."""

    def __init__(
        self,
        name: str,
        weights_path: str | None = None,
        label: str | None = None,
        device: str | None = None,
        *,
        env_var: str,
        default_config_path: str,
        config_loader: callable | None = None,
        path_resolver: PathResolver | None = None,
        yaml_loader: YamlLoader | None = None,
    ) -> None:
        super().__init__(
            name=name,
            weights_path=weights_path,
            label=label,
            device=device,
            config_loader=config_loader,
        )
        self._env_var = env_var
        self._default_config_path = default_config_path
        self._resolve_path = path_resolver or resolve_path
        self._load_yaml = yaml_loader or load_yaml
        self._records = self._load_records()

    def _resolve_config_path(self):
        env_path = os.environ.get(self._env_var)
        return self._resolve_path(env_path) if env_path else self._resolve_path(self._default_config_path)

    def _load_records(self) -> List[dict]:
        config_path = self._resolve_config_path()
        if not config_path:
            return []
        data = self._load_yaml(config_path)
        if not data:
            return []
        if isinstance(data, dict):
            camera_id = os.environ.get("EDGE_CAMERA_ID", "cam01")
            entries = data.get(camera_id)
            if entries is None:
                return []
            if not isinstance(entries, list):
                raise ValueError(f"{self._env_var} config for camera must be a list")
            return entries
        if not isinstance(data, list):
            raise ValueError(f"{self._env_var} config must be a list or camera-id mapping")
        return data

    def _get_records(self, frame: Any, metadata: Any) -> List[dict]:
        _ = (frame, metadata)
        return self._records

    @abstractmethod
    def _postprocess_records(self, records: List[dict], frame: Any, metadata: Any) -> List[EdgeDetection]:
        """Convert YAML records into edge detections."""

    def run(self, frame: Any, metadata: Any) -> List[EdgeDetection]:
        records = self._get_records(frame, metadata)
        if not records:
            return []
        return self._postprocess_records(records, frame, metadata)
