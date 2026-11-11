from __future__ import annotations

import logging
from pathlib import Path

from edge.pipeline.tasks.inference.device import normalize_device

LOGGER = logging.getLogger(__name__)


class BaseInferenceModel:
    """Base class for scheduled inference models.

    ScheduledInferenceEngine will instantiate model classes with
    name/weights_path/label/device. Subclass this to ensure compatibility.
    """

    def __init__(
        self,
        name: str,
        weights_path: str | None = None,
        label: str | None = None,
        device: str | None = None,
    ) -> None:
        self.name = name
        self.label = label or name
        self.weights_path = Path(weights_path).expanduser() if weights_path else None
        self.device = normalize_device(device)
        self._load_weights()

    def _load_weights(self) -> None:
        if self.weights_path:
            if self.weights_path.exists():
                LOGGER.info("%s loaded weights from %s", self.name, self.weights_path)
            else:
                LOGGER.warning("%s weights not found at %s (mock mode)", self.name, self.weights_path)
        else:
            LOGGER.info("%s initialized without weights (mock mode)", self.name)

    def run(self, frame, metadata):
        raise NotImplementedError("Model must implement run(frame, metadata)")

    def warmup(self, frame, metadata) -> None:
        """啟動時的暖機推理，預設沿用 run()。有狀態的模型（例如 tracker）要覆寫，讓暖機不影響之後的結果。"""
        self.run(frame, metadata)
