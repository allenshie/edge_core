from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List

from edge.schema import EdgeDetection

from ..model import BaseInferenceModel


class BaseEdgeModel(BaseInferenceModel, ABC):
    """Higher-level inference base class for reusable edge models."""

    def __init__(
        self,
        name: str,
        weights_path: str | None = None,
        label: str | None = None,
        device: str | None = None,
        *,
        config_loader: callable | None = None,
    ) -> None:
        super().__init__(name=name, weights_path=weights_path, label=label, device=device)
        self._config = config_loader(name) if config_loader else {}
        if self.device is None and self._config.get("device") is not None:
            self.device = self._config.get("device")

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @abstractmethod
    def run(self, frame: Any, metadata: Any) -> List[EdgeDetection]:
        """Run inference and return normalized edge detections."""
