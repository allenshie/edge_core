"""Cache helpers for scheduled inference results."""
from __future__ import annotations

import logging
from typing import List

from edge.schema import EdgeDetection

LOGGER = logging.getLogger(__name__)


def get_cached_results(context, name: str) -> List[EdgeDetection] | None:
    if context is None:
        return None
    cache = context.get_resource("inference_last_results") or {}
    results = cache.get(name)
    if results is None:
        LOGGER.debug("no cached results for task=%s", name)
    return results


def store_cached_results(context, name: str, results: List[EdgeDetection]) -> None:
    if context is None:
        return
    cache = context.get_resource("inference_last_results") or {}
    cache[name] = list(results)
    context.set_resource("inference_last_results", cache)


__all__ = ["get_cached_results", "store_cached_results"]
