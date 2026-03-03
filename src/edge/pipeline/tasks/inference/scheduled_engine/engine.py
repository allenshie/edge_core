"""Scheduled inference engine that reads per-phase model tasks."""
from __future__ import annotations

import importlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from smart_workflow import TaskContext, TaskError

from edge.pipeline.tasks.inference.engine import BaseInferenceEngine
from edge.pipeline.tasks.inference.device import normalize_device
from edge.schema import EdgeDetection

from .activity import (
    forklift_is_idle,
    has_forklift,
    idle_for_at_least,
    last_run_before_idle,
    update_forklift_activity,
)
from .cache import get_cached_results, store_cached_results
from .loader import extract_phase_entries, get_schedule_path, load_schedule_json, resolve_resource_root
from .models import ScheduledModelTask
from .policy import should_execute

LOGGER = logging.getLogger(__name__)

class ScheduledInferenceEngine(BaseInferenceEngine):
    """Engine that executes models based on phase-aware schedule."""

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._resource_root = self._resolve_resource_root()
        self._default_schedule = self._resource_root / "schedule.json"
        self._tasks_by_phase: Dict[str, List[ScheduledModelTask]] = self._load_schedule()
        self._active_phase: str | None = None
        self._forklift_source_tasks = self._parse_csv(
            os.environ.get("EDGE_FORKLIFT_SOURCE_TASKS", "detect_and_track")
        )
        self._forklift_class_names = self._parse_csv(os.environ.get("EDGE_FORKLIFT_CLASS_NAMES", "forklift"))
        self._forklift_min_score = float(os.environ.get("EDGE_FORKLIFT_MIN_SCORE", "0.4"))
        self._forklift_active_hold = float(os.environ.get("EDGE_FORKLIFT_ACTIVE_HOLD_SECONDS", "10"))
        self._forklift_idle_seconds = float(
            os.environ.get("EDGE_FORKLIFT_IDLE_SECONDS", str(self._forklift_active_hold))
        )

    def process(self, context: TaskContext) -> List[EdgeDetection]:
        phase = self._resolve_phase(context)
        tasks = self._tasks_by_phase.get(phase)
        if tasks is None:
            LOGGER.warning("no scheduled tasks for phase=%s", phase)
            return []
        if phase != self._active_phase:
            LOGGER.info("phase changed: %s -> %s", self._active_phase, phase)
            self._reset_phase_state(tasks)
            self._active_phase = phase

        detections: List[EdgeDetection] = []
        now = time.time()
        frame = context.get_resource("decoded_frame")
        metadata = {
            "phase": phase,
            "camera_id": context.config.camera.camera_id if context else "unknown",
            "context": context,
        }
        executed: List[str] = []
        reused: List[str] = []

        for task in tasks:
            if task.mode == "replay_last":
                if not self._should_execute(task, now, context):
                    if task.last_results:
                        detections.extend(task.last_results)
                        reused.append(task.name)
                    continue
                cached = self._get_cached_results(context, task.source_task or task.name)
                if cached is None:
                    continue
                task.last_results = list(cached)
                detections.extend(task.last_results)
                task.last_run = now
                executed.append(task.name)
                continue
            if task.mode == "interval_when_idle":
                if not self._should_execute(task, now, context):
                    if self._forklift_is_idle(context, now) and task.last_results:
                        detections.extend(task.last_results)
                        reused.append(task.name)
                    continue
                LOGGER.debug("running scheduled task=%s (mode=%s)", task.name, task.mode)
                task_results = task.model.run(frame, metadata=metadata)
                task.last_results = task_results
                detections.extend(task_results)
                task.last_run = now
                executed.append(task.name)
                self._store_cached_results(context, task.name, task_results)
                self._update_forklift_activity(context, task.name, task_results, now)
                continue
            if not self._should_execute(task, now, context):
                if task.mode in {"interval", "run_once_after_switch", "replay_last"} and task.last_results:
                    detections.extend(task.last_results)
                    reused.append(task.name)
                continue
            LOGGER.debug("running scheduled task=%s (mode=%s)", task.name, task.mode)
            task_results = task.model.run(frame, metadata=metadata)
            task.last_results = task_results
            detections.extend(task_results)
            task.last_run = now
            if task.mode == "run_once_after_switch":
                task.run_once_completed = True
            executed.append(task.name)
            self._store_cached_results(context, task.name, task_results)
            self._update_forklift_activity(context, task.name, task_results, now)
        if context is not None:
            context.set_resource("inference_models_run", executed)
            context.set_resource("inference_models_reuse", reused)
        if executed or reused:
            LOGGER.info("scheduled tasks (phase=%s): run=%s reuse=%s", phase, executed, reused)
        return detections

    # --- schedule helpers -------------------------------------------------

    def _resolve_resource_root(self) -> Path:
        return resolve_resource_root()

    def _load_schedule(self) -> Dict[str, List[ScheduledModelTask]]:
        schedule_path = self._get_schedule_path()
        data = load_schedule_json(schedule_path)

        tasks_by_phase: Dict[str, List[ScheduledModelTask]] = {}
        for phase, definition in data.items():
            entries = self._extract_phase_entries(definition)
            tasks_by_phase[phase] = [self._build_task(entry) for entry in entries]
        LOGGER.info("loaded schedule from %s (phases=%d)", schedule_path, len(tasks_by_phase))
        return tasks_by_phase

    def _extract_phase_entries(self, definition: Any) -> List[Dict[str, Any]]:
        return extract_phase_entries(definition)

    def _get_schedule_path(self) -> Path:
        return get_schedule_path(self._resource_root, self._default_schedule)

    def _build_task(self, entry: Dict[str, Any]) -> ScheduledModelTask:
        mode = entry.get("mode", "every_frame")
        interval = float(entry.get("interval_seconds") or 0.0)
        min_interval = float(entry.get("min_interval_seconds") or 0.0)
        if mode == "replay_last":
            source_task = entry.get("source_task") or entry.get("name")
            if not source_task:
                raise TaskError("replay_last 需要 source_task 或 name")
            return ScheduledModelTask(
                name=entry.get("name", source_task),
                mode=mode,
                interval_seconds=interval,
                model=None,
                source_task=source_task,
                min_interval_seconds=min_interval,
            )
        model = self._instantiate_model(entry)
        return ScheduledModelTask(
            name=entry.get("name", model.__class__.__name__),
            mode=mode,
            interval_seconds=interval,
            model=model,
            min_interval_seconds=min_interval,
        )

    def _instantiate_model(self, entry: Dict[str, Any]):
        class_path = entry.get("model_class")
        if not class_path:
            raise TaskError("schedule entry 缺少 model_class 欄位")
        module_name, class_name = self._split_class_path(class_path)
        module = importlib.import_module(module_name)
        model_cls = getattr(module, class_name, None)
        if model_cls is None:
            raise TaskError(f"找不到模型類別：{class_path}")

        weights = self._resolve_weights(entry)
        device = normalize_device(entry.get("device") or os.environ.get("EDGE_MODEL_DEVICE"))
        kwargs = {
            "name": entry.get("name", class_name),
            "weights_path": weights,
            "label": entry.get("label"),
            "device": device,
        }
        try:
            return model_cls(**kwargs)
        except TypeError as exc:
            if "device" in kwargs and "device" in str(exc):
                kwargs.pop("device", None)
                return model_cls(**kwargs)
            raise TaskError(f"載入模型失敗（{class_path}）：{exc}") from exc

    def _resolve_weights(self, entry: Dict[str, Any]) -> str | None:
        if entry.get("weights_path"):
            return self._resolve_path(entry["weights_path"])
        env_key = entry.get("weights_env")
        if env_key and os.environ.get(env_key):
            return self._resolve_path(os.environ[env_key])
        return None

    def _resolve_path(self, raw_path: str) -> str:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (self._resource_root / raw_path).resolve()
        return str(path)

    def _split_class_path(self, path: str) -> tuple[str, str]:
        if ":" in path:
            module_name, class_name = path.split(":", 1)
            return module_name, class_name
        if "." in path:
            module, cls = path.rsplit(".", 1)
            return module, cls
        raise TaskError(f"model_class 格式不正確：{path}")

    # --- execution helpers ------------------------------------------------

    def _resolve_phase(self, context: TaskContext) -> str:
        phase = context.get_resource("edge_mode")
        if not phase:
            phase = (
                os.environ.get("EDGE_MODE_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT")
                or os.environ.get("EDGE_DEMO_DEFAULT_PHASE")
                or "working_stage_1"
            )
        return str(phase)

    def _reset_phase_state(self, tasks: Sequence[ScheduledModelTask]) -> None:
        for task in tasks:
            task.last_run = None
            task.run_once_completed = False
            task.last_results = []

    def _should_execute(self, task: ScheduledModelTask, now: float, context: TaskContext | None) -> bool:
        return should_execute(task=task, now=now, context=context, engine=self)

    def _get_cached_results(self, context: TaskContext | None, name: str) -> List[EdgeDetection] | None:
        return get_cached_results(context, name)

    def _store_cached_results(
        self, context: TaskContext | None, name: str, results: List[EdgeDetection]
    ) -> None:
        store_cached_results(context, name, results)

    def _update_forklift_activity(
        self,
        context: TaskContext | None,
        task_name: str,
        results: List[EdgeDetection],
        now: float,
    ) -> None:
        update_forklift_activity(self, context, task_name, results, now)

    def _forklift_is_idle(self, context: TaskContext | None, now: float) -> bool:
        return forklift_is_idle(self, context, now)

    def _last_run_before_idle(self, context: TaskContext | None, last_run: float) -> bool:
        return last_run_before_idle(context, last_run)

    def _idle_for_at_least(self, context: TaskContext | None, seconds: float, now: float) -> bool:
        return idle_for_at_least(context, seconds, now)

    def _has_forklift(self, results: List[EdgeDetection]) -> bool:
        return has_forklift(self, results)

    def _parse_csv(self, raw: str | None) -> List[str]:
        if not raw:
            return []
        return [item.strip().lower() for item in raw.split(",") if item.strip()]


__all__ = ["ScheduledInferenceEngine"]
