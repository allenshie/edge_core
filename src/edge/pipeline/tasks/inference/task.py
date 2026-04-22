"""Inference task delegating to pluggable engines."""
from __future__ import annotations

import importlib
import os
import time
from datetime import datetime, timezone
from typing import Sequence, Type

from smart_workflow import BaseTask, TaskContext, TaskError, TaskResult

from edge.runtime.stage_logging import emit_task_health
from edge.runtime.rate_meter import RateMeter
from edge.api.mode_server import MODE_RESOURCE
from edge.schema import FrameMeta, StageStats

from .engine import BaseInferenceEngine, DefaultInferenceEngine


class InferenceTask(BaseTask):
    name = "edge-inference"

    def __init__(self, context: TaskContext | None = None) -> None:
        self._engine = self._load_engine(context)
        self._stats = StageStats(task_name="infer")
        self._infer_rate = RateMeter()
        self._last_infer_frame_seq: int | None = None
        self._last_detection_count = 0
        self._last_health_snapshot: dict | None = None

    def run(self, context: TaskContext) -> TaskResult:
        start = time.perf_counter()
        frame = context.get_resource("decoded_frame")
        frame_meta = context.get_resource("frame_meta")
        phase = self._resolve_phase(context)
        camera_id = context.config.camera.camera_id if context and context.config and context.config.camera else "unknown"
        outcome = self._engine.process(
            frame,
            phase=phase,
            metadata={"phase": phase, "camera_id": camera_id},
        )
        detections = outcome.detections
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        context.set_resource("inference_output", detections)
        context.set_resource("inference_models_run", list(outcome.models_run))
        context.set_resource("inference_models_reuse", list(outcome.models_reuse))
        self._last_detection_count = len(detections)
        if isinstance(frame_meta, FrameMeta) and frame_meta.frame_seq != self._last_infer_frame_seq:
            self._infer_rate.mark(frame_seq=frame_meta.frame_seq, ts=frame_meta.capture_ts)
            self._last_infer_frame_seq = frame_meta.frame_seq
        self._stats.record_success(
            session_id=frame_meta.session_id if isinstance(frame_meta, FrameMeta) else None,
            frame_seq=frame_meta.frame_seq if isinstance(frame_meta, FrameMeta) else None,
            capture_ts=frame_meta.capture_ts if isinstance(frame_meta, FrameMeta) else None,
            success_ts=datetime.now(timezone.utc),
            latency_ms=elapsed_ms,
            worker_alive=True,
        )
        self._report_health(context, frame_meta, detections)
        frame_meta_payload = frame_meta.to_dict() if isinstance(frame_meta, FrameMeta) else None
        payload = {"detections": detections}
        if frame_meta_payload is not None:
            payload["frame_meta"] = frame_meta_payload
        return TaskResult(payload=payload)

    def _resolve_phase(self, context: TaskContext) -> str:
        phase = context.get_resource(MODE_RESOURCE)
        if not phase:
            phase = (
                os.environ.get("EDGE_MODE_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT_PHASE")
                or "working_stage_1"
            )
        return str(phase)

    def _report_health(
        self,
        context: TaskContext,
        frame_meta: FrameMeta | object,
        detections: Sequence,
    ) -> None:
        report_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
        stale_threshold = float(getattr(context.config, "health_stale_threshold_seconds", 0.0) or 0.0)
        model_cfg = getattr(context.config, "model", None)
        capture_age_s = frame_meta.age_seconds() if isinstance(frame_meta, FrameMeta) else None
        infer_fps = self._infer_rate.fps()
        summary_fields = {
            "model": getattr(model_cfg, "name", None),
            "device": getattr(model_cfg, "device", None),
            "detections": len(detections),
        }
        health_state = "degraded" if stale_threshold > 0 and capture_age_s is not None and capture_age_s >= stale_threshold else "ok"
        snapshot = {
            "stage": "infer",
            "state": health_state,
            "session_id": self._stats.session_id,
            "frame_seq": self._stats.last_frame_seq,
            "capture_fps": None,
            "infer_fps": infer_fps,
            "stream_output_fps": None,
            "stream_unique_fps": None,
            "age_s": capture_age_s,
            "alive": bool(self._stats.worker_alive),
            "note": f"model={getattr(model_cfg, 'name', None)} device={getattr(model_cfg, 'device', None)} detections={len(detections)}",
        }
        line = emit_task_health(
            context,
            self._stats,
            health_state=health_state,
            reason="stale_frame" if health_state == "degraded" else None,
            worker_alive=True,
            extra_fields=summary_fields,
            report_interval_seconds=report_interval,
            force=False,
            rate_meter=self._infer_rate,
            rate_prefix="infer",
        )
        if line is not None:
            self._last_health_snapshot = snapshot

    def health_snapshot(self, context: TaskContext | None = None) -> dict:
        if self._last_health_snapshot is not None:
            return dict(self._last_health_snapshot)
        model_cfg = getattr(self._engine, "_model_config", None)
        return {
            "stage": "infer",
            "state": self._stats.health_state,
            "session_id": self._stats.session_id,
            "frame_seq": self._stats.last_frame_seq,
            "capture_fps": None,
            "infer_fps": self._infer_rate.fps(),
            "stream_output_fps": None,
            "stream_unique_fps": None,
            "age_s": self._stats.capture_age_seconds(),
            "alive": bool(self._stats.worker_alive),
            "note": f"model={getattr(model_cfg, 'name', None)} device={getattr(model_cfg, 'device', None)} detections={self._last_detection_count}",
        }

    def _load_engine(self, context: TaskContext | None) -> BaseInferenceEngine:
        engine_path = getattr(context.config, "inference_engine_class", None) if context else None
        if not engine_path:
            return DefaultInferenceEngine(context=context)
        engine_cls = self._import_engine(engine_path)
        try:
            return engine_cls(context=context)
        except TypeError:
            return engine_cls()

    def _import_engine(self, path: str) -> Type[BaseInferenceEngine]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
        elif "." in path:
            module_name, class_name = path.rsplit(".", 1)
        else:
            raise TaskError(f"無法解析 Inference Engine：{path}")
        module = importlib.import_module(module_name)
        engine_cls = getattr(module, class_name, None)
        if engine_cls is None or not issubclass(engine_cls, BaseInferenceEngine):
            raise TaskError(f"{class_name} 必須繼承 BaseInferenceEngine")
        return engine_cls

    def close(self, context: TaskContext) -> list[dict]:
        _ = context
        close_fn = getattr(self._engine, "close", None)
        if callable(close_fn):
            result = close_fn()
            if isinstance(result, list):
                return result
            if result is not None:
                return [result]
        return []
