"""Cache helpers for scheduled inference results."""
from __future__ import annotations

import logging
from typing import List

from edge.schema import EdgeDetection

LOGGER = logging.getLogger(__name__)


def get_cached_results(engine, name: str) -> List[EdgeDetection] | None:
    if engine is None:
        return None
    cache = getattr(engine, "_cached_results", {})
    results = cache.get(name)
    if results is None:
        LOGGER.debug("no cached results for task=%s", name)
    return results


def store_cached_results(engine, name: str, results: List[EdgeDetection]) -> None:
    if engine is None:
        return
    cache = getattr(engine, "_cached_results", None)
    if cache is None:
        cache = {}
        setattr(engine, "_cached_results", cache)
    cache[name] = list(results)


__all__ = ["get_cached_results", "store_cached_results"]
