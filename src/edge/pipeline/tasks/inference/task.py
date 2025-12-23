"""推理佔位邏輯。"""
from __future__ import annotations

import time
import json
import logging
from pathlib import Path
from typing import Any, List

import cv2  # type: ignore[import]
from ultralytics import YOLO

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult
from edge.schema import EdgeDetection

LOGGER = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = PACKAGE_ROOT.parent.parent / "output_frames"


class InferenceTask(BaseTask):
    name = "edge-inference"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._model = None
        self._model_config = context.config.model if context else None
        self._visual_config = context.config.visualization if context else None
        self._show_warning_logged = False

    def _ensure_model(self, context: TaskContext) -> None:
        if self._model is not None:
            return

        model_cfg = self._model_config or context.config.model
        try:
            model = YOLO(model_cfg.weights_path)
            if model_cfg.device:
                model.to(model_cfg.device)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error("無法載入 YOLO 模型 (%s): %s", model_cfg.weights_path, exc)
            raise TaskError(f"載入模型失敗: {exc}") from exc

        LOGGER.info("YOLO 模型載入成功：%s", model_cfg.weights_path)
        self._model = model
        self._model_config = model_cfg

    def run(self, context: TaskContext) -> TaskResult:  # type: ignore[override]
        st = time.time()
        frame = context.get_resource("decoded_frame")
        frame_path = context.get_resource("decoded_frame_path")
        if frame is None:
            LOGGER.warning("沒有待推理的 frame，跳過")
            return TaskResult(payload={"detections": []})

        self._ensure_model(context)
        assert self._model is not None  # for mypy

        threshold = (
            self._model_config.confidence_threshold  # type: ignore[union-attr]
            if self._model_config
            else context.config.model.confidence_threshold
        )
        visualize = (
            self._model_config.visualize  # type: ignore[union-attr]
            if self._model_config is not None
            else context.config.model.visualize
        )
        visual_cfg = self._visual_config or context.config.visualization
        should_render = visualize and visual_cfg.enabled

        results = self._model.track(frame, verbose=False)
        detections = self._parse_results(results, threshold)
        # detections = []
        context.set_resource("inference_output", detections)
        output_path = self._render_frame(frame, detections, frame_path, visual_cfg, should_render)
        context.set_resource("frame_path", output_path)
        LOGGER.debug("推理結果：%s", json.dumps([det.to_dict() for det in detections]))
        print(f"[InferenceTask] 推理耗時: {time.time() - st:.3f} 秒, 偵測到 {len(detections)} 個物件")
        return TaskResult(payload={"detections": detections, "visualized_frame": output_path})

    def _parse_results(self, results: Any, threshold: float) -> List[EdgeDetection]:
        detections: List[EdgeDetection] = []
        if not results:
            return detections
        result = results[0]
        boxes = getattr(result, "boxes", None)
        names = getattr(result, "names", None) or getattr(getattr(result, "model", None), "names", {}) or {}
        if boxes is None:
            return detections

        xyxy_list = boxes.xyxy.cpu().tolist() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy.tolist()
        conf_list = boxes.conf.cpu().tolist() if hasattr(boxes.conf, "cpu") else boxes.conf.tolist()
        cls_list = boxes.cls.cpu().tolist() if hasattr(boxes.cls, "cpu") else boxes.cls.tolist()
        id_list = None
        if hasattr(boxes, "id") and boxes.id is not None:
            id_list = boxes.id.cpu().tolist() if hasattr(boxes.id, "cpu") else boxes.id.tolist()

        for idx, xyxy in enumerate(xyxy_list):
            conf = float(conf_list[idx]) if idx < len(conf_list) else 0.0
            if conf < threshold:
                continue
            bbox = [int(x) for x in xyxy]
            cls_id = int(cls_list[idx]) if idx < len(cls_list) and cls_list[idx] is not None else -1
            class_name = names.get(cls_id, str(cls_id))
            track_id = None
            if id_list and idx < len(id_list):
                try:
                    track_id = int(id_list[idx]) if id_list[idx] is not None else None
                except Exception:  # pylint: disable=broad-except
                    track_id = None

            detections.append(
                EdgeDetection(
                    track_id=track_id,
                    class_name=class_name,
                    bbox=bbox,
                    score=conf,
                )
            )
        return detections

    def _render_frame(
        self,
        frame,
        detections: List[EdgeDetection],
        base_path: str | None,
        visual_cfg,
        should_render: bool,
    ) -> str | None:
        if not should_render:
            return None

        vis_frame = self._draw_results(frame, detections)
        mode = (visual_cfg.mode or "write").lower()

        if mode == "show":
            try:
                resized = cv2.resize(vis_frame, (visual_cfg.window_width, visual_cfg.window_height))
                cv2.imshow(visual_cfg.window_name, resized)
                cv2.waitKey(1)
            except Exception as exc:  # pragma: no cover - GUI-only path
                if not self._show_warning_logged:
                    LOGGER.warning("無法顯示視覺化視窗：%s", exc)
                    self._show_warning_logged = True
            return None

        # default to write mode
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if base_path:
            output_path = Path(base_path).with_name("latest_inference.jpg")
        else:
            output_path = OUTPUT_DIR / "latest_inference.jpg"
        cv2.imwrite(str(output_path), vis_frame)
        LOGGER.info("推理可視化輸出：%s", output_path)
        return str(output_path)

    def _draw_results(self, frame, detections: List[EdgeDetection]):
        vis_frame = frame.copy()
        for det in detections:
            bbox = det.bbox or [0, 0, vis_frame.shape[1] // 2, vis_frame.shape[0] // 2]
            x1, y1, x2, y2 = bbox
            score = det.score
            label = f"{det.class_name}:{score:.2f}"
            cv2.rectangle(vis_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(vis_frame, label, (x1, max(y1 - 5, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        return vis_frame
