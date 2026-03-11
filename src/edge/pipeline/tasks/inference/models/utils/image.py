from __future__ import annotations

import numpy as np


def calculate_mean_brightness(image: np.ndarray) -> float:
    """Return mean brightness for RGB/BGR or single-channel images."""
    if image is None or image.size == 0:
        return 0.0
    if len(image.shape) == 3 and image.shape[2] == 3:
        gray = 0.299 * image[:, :, 0] + 0.587 * image[:, :, 1] + 0.114 * image[:, :, 2]
        return float(np.mean(gray))
    return float(np.mean(image))
