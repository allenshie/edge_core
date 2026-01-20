"""Scheduled inference engine that reads per-phase model tasks."""
from __future__ import annotations

import importlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence

from smart_workflow import TaskContext, TaskError

from edge.pipeline.tasks.inference.engine import BaseInferenceEngine
from edge.schema import EdgeDetection

LOGGER = logging.getLogger(__name__)


@dataclass
class ScheduledModelTask:
    name: str
    mode: str
    interval_seconds: float
    model: Any
    last_run: float | None = None
    run_once_completed: bool = False
    last_results: List[EdgeDetection] = field(default_factory=list)


class ScheduledInferenceEngine(BaseInferenceEngine):
    """Engine that executes models based on phase-aware schedule."""

    def __init__(self, context: TaskContext | None = None) -> None:
        super().__init__(context)
        self._resource_root = self._resolve_resource_root()
        self._default_schedule = self._resource_root / "schedule.json"
        self._tasks_by_phase: Dict[str, List[ScheduledModelTask]] = self._load_schedule()
        self._active_phase: str | None = None

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
            if not self._should_execute(task, now):
                if task.mode in {"interval", "run_once_after_switch"} and task.last_results:
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
        if executed or reused:
            LOGGER.info("scheduled tasks (phase=%s): run=%s reuse=%s", phase, executed, reused)
        return detections

    # --- schedule helpers -------------------------------------------------

    def _resolve_resource_root(self) -> Path:
        root = os.environ.get("EDGE_RESOURCE_ROOT")
        if not root:
            return Path.cwd()
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        return candidate

    def _load_schedule(self) -> Dict[str, List[ScheduledModelTask]]:
        schedule_path = self._get_schedule_path()
        if not schedule_path.exists():
            raise TaskError(f"找不到排程設定檔：{schedule_path}")
        try:
            data = json.loads(schedule_path.read_text())
        except json.JSONDecodeError as exc:
            raise TaskError(f"排程設定檔格式錯誤：{exc}") from exc

        tasks_by_phase: Dict[str, List[ScheduledModelTask]] = {}
        for phase, entries in data.items():
            tasks_by_phase[phase] = [self._build_task(entry) for entry in entries]
        LOGGER.info("loaded schedule from %s (phases=%d)", schedule_path, len(tasks_by_phase))
        return tasks_by_phase

    def _get_schedule_path(self) -> Path:
        env_path = os.environ.get("EDGE_SCHEDULE_PATH") or os.environ.get("EDGE_DEMO_SCHEDULE_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if not candidate.is_absolute():
                candidate = (self._resource_root / env_path).resolve()
            return candidate
        return self._default_schedule

    def _build_task(self, entry: Dict[str, Any]) -> ScheduledModelTask:
        mode = entry.get("mode", "every_frame")
        interval = float(entry.get("interval_seconds") or 0.0)
        model = self._instantiate_model(entry)
        return ScheduledModelTask(
            name=entry.get("name", model.__class__.__name__),
            mode=mode,
            interval_seconds=interval,
            model=model,
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
        kwargs = {
            "name": entry.get("name", class_name),
            "weights_path": weights,
            "label": entry.get("label"),
        }
        try:
            return model_cls(**kwargs)
        except TypeError as exc:
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

    def _should_execute(self, task: ScheduledModelTask, now: float) -> bool:
        if task.mode == "every_frame":
            return True
        if task.mode == "interval":
            return task.last_run is None or (now - task.last_run) >= task.interval_seconds
        if task.mode == "run_once_after_switch":
            return not task.run_once_completed
        LOGGER.warning("unknown scheduled mode=%s for task=%s", task.mode, task.name)
        return False


__all__ = ["ScheduledInferenceEngine"]
