from __future__ import annotations

from typing import List


def compute_bbox_from_polygon(polygon: List[List[float]]) -> List[float]:
    """Compute bounding box [x1, y1, x2, y2] from polygon vertices."""
    if not polygon:
        return []
    xs = [pt[0] for pt in polygon]
    ys = [pt[1] for pt in polygon]
    return [min(xs), min(ys), max(xs), max(ys)]
