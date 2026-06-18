"""Detection overlay helpers for streaming output frames."""
from __future__ import annotations

from typing import Any, Sequence

import cv2

from edge.schema import EdgeDetection


def _truncate_text(text: str, max_length: int) -> str:
    value = str(text).strip() or "unknown"
    if max_length <= 0 or len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]
    return value[: max_length - 3].rstrip() + "..."


def _measure_text_width(text: str, font_scale: float, text_thickness: int) -> int:
    text_size, _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, text_thickness)
    return text_size[0]


def _compose_detection_label(class_name: str, suffix_parts: tuple[str, ...]) -> str:
    parts = [class_name, *suffix_parts]
    return " ".join(part for part in parts if part).strip()


def _fit_detection_label(
    class_name: str,
    suffix_parts: tuple[str, ...],
    *,
    max_width_px: int,
    font_scale: float,
    text_thickness: int,
) -> tuple[str, bool]:
    candidate = _compose_detection_label(class_name, suffix_parts)
    if _measure_text_width(candidate, font_scale, text_thickness) <= max_width_px:
        return candidate, True

    last_candidate = candidate
    for class_limit in range(len(class_name) - 1, 0, -1):
        truncated_class = _truncate_text(class_name, class_limit)
        candidate = _compose_detection_label(truncated_class, suffix_parts)
        last_candidate = candidate
        if _measure_text_width(candidate, font_scale, text_thickness) <= max_width_px:
            return candidate, True
    return last_candidate, _measure_text_width(last_candidate, font_scale, text_thickness) <= max_width_px


def _fit_plain_label(
    label: str,
    *,
    max_width_px: int,
    font_scale: float,
    text_thickness: int,
) -> str:
    if _measure_text_width(label, font_scale, text_thickness) <= max_width_px:
        return label

    last_candidate = label
    for label_limit in range(len(label) - 1, 0, -1):
        candidate = _truncate_text(label, label_limit)
        last_candidate = candidate
        if _measure_text_width(candidate, font_scale, text_thickness) <= max_width_px:
            return candidate
    return last_candidate


def _format_detection_label(
    det: EdgeDetection,
    *,
    show_track_info: bool,
    matching_label: str | None = None,
    max_width_px: int | None = None,
    font_scale: float = 0.4,
    text_thickness: int = 1,
) -> str:
    if matching_label is not None:
        label = _truncate_text(matching_label, 24)
        if max_width_px is None or max_width_px <= 0:
            return label
        return _fit_plain_label(
            label,
            max_width_px=max_width_px,
            font_scale=font_scale,
            text_thickness=text_thickness,
        )

    score = det.score if det.score is not None else det.bbox_confidence_score
    score_value = float(score) if score is not None else 0.0
    class_name = _truncate_text(det.class_name, 18)
    score_text = f"{max(0, min(100, int(round(score_value * 100))))}%"
    track_text = f"#{det.track_id}" if det.track_id is not None else None

    if show_track_info and track_text is not None:
        suffix_variants: tuple[tuple[str, ...], ...] = ((track_text, score_text), (track_text,), (score_text,), ())
    else:
        suffix_variants = ((score_text,), ())

    if max_width_px is None or max_width_px <= 0:
        return _compose_detection_label(class_name, suffix_variants[0])

    for suffix_parts in suffix_variants:
        label, fits = _fit_detection_label(
            class_name,
            suffix_parts,
            max_width_px=max_width_px,
            font_scale=font_scale,
            text_thickness=text_thickness,
        )
        if fits:
            return label

    # 所有候選都超寬時，回傳最短版本，讓畫面至少保留可辨識的類別名稱。
    return _fit_detection_label(
        class_name,
        suffix_variants[-1],
        max_width_px=max_width_px,
        font_scale=font_scale,
        text_thickness=text_thickness,
    )[0]


def _draw_detection_box_and_label(
    vis_frame: Any,
    bbox: Sequence[int],
    label: str,
    *,
    color: tuple[int, int, int] = (0, 255, 0),
    text_color: tuple[int, int, int] = (255, 255, 255),
    font_scale: float = 0.4,
    thickness: int = 1,
    text_thickness: int = 1,
) -> None:
    frame_h, frame_w = vis_frame.shape[:2]
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return

    try:
        x1, y1, x2, y2 = [int(v) for v in bbox]
    except (TypeError, ValueError):
        return

    font_face = cv2.FONT_HERSHEY_SIMPLEX
    label_padding_x = max(4, thickness * 2)
    label_padding_y = max(2, thickness)

    text_size, baseline = cv2.getTextSize(label, font_face, font_scale, text_thickness)
    text_w, text_h = text_size
    label_w = text_w + label_padding_x * 2
    label_h = text_h + baseline + label_padding_y * 2

    # 優先把 label 放在 bbox 上方；若空間不足，則改放到 bbox 下方並在畫面內對齊。
    label_x1 = max(0, min(x1, max(frame_w - label_w, 0)))
    label_x2 = min(frame_w, label_x1 + label_w)
    if y1 >= label_h:
        label_y2 = y1
        label_y1 = y1 - label_h
    else:
        label_y1 = min(max(y2, 0), max(frame_h - label_h, 0))
        label_y2 = min(frame_h, label_y1 + label_h)

    text_org_x = label_x1 + label_padding_x
    text_org_y = label_y2 - label_padding_y - baseline

    cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, thickness)
    cv2.rectangle(vis_frame, (label_x1, label_y1), (label_x2, label_y2), color, -1)
    cv2.putText(
        vis_frame,
        label,
        (text_org_x, text_org_y),
        font_face,
        font_scale,
        text_color,
        text_thickness,
    )
