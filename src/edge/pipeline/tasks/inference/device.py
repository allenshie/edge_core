from __future__ import annotations

import logging
import os

LOGGER = logging.getLogger(__name__)


def normalize_device(device: str | None) -> str | None:
    if not device:
        return None
    fallback = os.environ.get("EDGE_MODEL_DEVICE_FALLBACK", "cpu").strip().lower()
    device = device.strip()
    if not device.startswith("cuda"):
        return device

    try:
        import torch  # type: ignore
    except Exception:  # pylint: disable=broad-except
        return _handle_fallback(device, fallback, "CUDA requested but torch is unavailable")

    if not torch.cuda.is_available():
        return _handle_fallback(device, fallback, "CUDA requested but no GPU is available")

    if ":" in device:
        try:
            index = int(device.split(":", 1)[1])
        except ValueError:
            index = None
        if index is not None and index >= torch.cuda.device_count():
            return _handle_fallback(device, fallback, "CUDA device index out of range")
    return device


def _handle_fallback(device: str, fallback: str, message: str) -> str | None:
    if fallback == "none":
        raise ValueError(f"{message}: {device}")
    LOGGER.warning("%s; fallback to %s", message, fallback)
    return "cpu" if fallback == "cpu" else None
