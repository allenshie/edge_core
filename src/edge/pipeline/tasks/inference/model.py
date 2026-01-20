from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class BaseInferenceModel:
    """Base class for scheduled inference models.

    ScheduledInferenceEngine will instantiate model classes with
    name/weights_path/label. Subclass this to ensure compatibility.
    """

    def __init__(self, name: str, weights_path: str | None = None, label: str | None = None) -> None:
        self.name = name
        self.label = label or name
        self.weights_path = Path(weights_path).expanduser() if weights_path else None
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
