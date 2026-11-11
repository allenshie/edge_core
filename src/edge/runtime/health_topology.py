"""Edge 的健康架構定義（spec-edge.md 第 2 節）。

在程式啟動時、啟動任務之前建立。enabled 與依賴的 callable 從 context resource
取得 Engine；Engine 還沒建立時 enabled 回傳 True、依賴回傳 None，任何例外也一樣處理，
callable 不會阻塞。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from smart_health_record import HealthMonitor
from smart_workflow import TaskContext

from edge.messaging import EDGE_EVENTS_ROUTE, MESSAGING_CLIENT_RESOURCE
from edge.runtime.health_settings import HealthSettings

LOGGER = logging.getLogger(__name__)

SERVICE = "edge"
STARTUP_GRACE_S = 30.0
ERROR_WINDOW_S = 30.0
INFER_STUCK_AFTER_S = 10.0

LANES = (
    ("main", "主迴圈", True),
    ("ingest-bg", "取流背景執行緒", False),
    ("stream-bg", "推流輸出執行緒", False),
)


@dataclass(frozen=True)
class NodeDefinition:
    node_id: str
    label: str
    lane: str
    upstream: tuple[str, ...] = ()
    dependencies: Mapping[str, Callable[[], bool | None]] = field(default_factory=dict)
    enabled: bool | Callable[[], bool] = True
    stale_after_s: float | None = None
    stuck_after_s: float | None = None
    stale_reason: str = "stale_success"
    startup_grace_s: float = STARTUP_GRACE_S
    error_window_s: float = ERROR_WINDOW_S


def derive_stale_after(
    poll_interval: float,
    override: float | None,
    *,
    infer_stuck_after_s: float = INFER_STUCK_AFTER_S,
) -> float:
    """主迴圈節點的 stale 門檻：max(5, 3 × 每輪間隔, infer 的 stuck 門檻 + 1)。

    每輪間隔為 0 時以 1 秒計算；有覆寫時用覆寫值。main 是 sequential lane，推理卡住時
    其他節點也沒有新的成功，stale 門檻要比 infer 的 stuck 門檻長，才不會先由別的節點轉紅。
    """
    if override is not None:
        return float(override)
    interval = float(poll_interval or 0.0)
    if interval <= 0:
        interval = 1.0
    return max(5.0, 3.0 * interval, infer_stuck_after_s + 1.0)


def _find_node(context: TaskContext, task_type: type) -> Any:
    pipeline = context.get_resource("edge_pipeline")
    find = getattr(pipeline, "find_node", None)
    return find(task_type) if callable(find) else None


def _guard_enabled(fn: Callable[[], bool]) -> Callable[[], bool]:
    def _enabled() -> bool:
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 - 判斷失敗時不停用節點
            LOGGER.debug("health enabled callable failed", exc_info=True)
            return True

    return _enabled


def _guard_dependency(fn: Callable[[], bool | None]) -> Callable[[], bool | None]:
    def _dependency() -> bool | None:
        try:
            value = fn()
        except Exception:  # noqa: BLE001 - 判斷失敗時視為無法判斷
            LOGGER.debug("health dependency callable failed", exc_info=True)
            return None
        return value if isinstance(value, bool) else None

    return _dependency


def _callables(context: TaskContext) -> dict[str, Callable[[], Any]]:
    from edge.pipeline.tasks.inference.task import InferenceTask
    from edge.pipeline.tasks.streaming.task import StreamingTask

    def infer_enabled() -> bool:
        task = _find_node(context, InferenceTask)
        if task is None:
            return True
        return bool(task.engine.has_work_for(task.current_phase(context)))

    def stream_enabled() -> bool:
        task = _find_node(context, StreamingTask)
        if task is None:
            return True
        if not task.enabled:
            return False
        engine = task.engine
        if engine is None:
            return True
        return bool(engine.wants_stream_for(task.current_phase(context)))

    def stream_sink() -> bool | None:
        task = _find_node(context, StreamingTask)
        engine = getattr(task, "engine", None)
        return None if engine is None else engine.sink_connected()

    def messaging() -> bool | None:
        client = context.get_resource(MESSAGING_CLIENT_RESOURCE)
        if client is None:
            return None
        return client.is_connected(EDGE_EVENTS_ROUTE)

    return {
        "infer": _guard_enabled(infer_enabled),
        "stream": _guard_enabled(stream_enabled),
        "stream_sink": _guard_dependency(stream_sink),
        "messaging": _guard_dependency(messaging),
    }


def node_definitions(context: TaskContext, settings: HealthSettings) -> list[NodeDefinition]:
    config = context.config
    derived = derive_stale_after(getattr(config, "poll_interval", 0.0), settings.stale_after_override)
    publish_enabled = bool(getattr(getattr(config, "publish", None), "enabled", True))
    fns = _callables(context)
    return [
        NodeDefinition("ingest-worker", "取流執行緒", "ingest-bg", stale_after_s=5.0, stuck_after_s=20.0,
                       stale_reason="no_new_frame"),
        NodeDefinition("ingest", "取流", "main", upstream=("ingest-worker",), stale_after_s=derived,
                       stuck_after_s=20.0),
        NodeDefinition("infer", "推理", "main", upstream=("ingest",), enabled=fns["infer"],
                       stale_after_s=derived, stuck_after_s=INFER_STUCK_AFTER_S),
        NodeDefinition("stream", "推流", "main", upstream=("infer",), enabled=fns["stream"],
                       stale_after_s=derived, stuck_after_s=5.0),
        NodeDefinition("stream-output", "推流輸出", "stream-bg", upstream=("stream",),
                       dependencies={"stream_sink": fns["stream_sink"]}, enabled=fns["stream"],
                       stale_after_s=5.0, stuck_after_s=10.0),
        NodeDefinition("publish", "發布", "main", upstream=("infer",),
                       dependencies={"messaging": fns["messaging"]}, enabled=publish_enabled,
                       stale_after_s=derived, stuck_after_s=10.0),
    ]


def build_health_monitor(
    context: TaskContext,
    settings: HealthSettings,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> HealthMonitor:
    camera = getattr(getattr(context.config, "camera", None), "camera_id", None) or "unknown"
    monitor = HealthMonitor(SERVICE, str(camera), wait_for_ready=True, clock=clock)
    for lane_id, label, sequential in LANES:
        monitor.add_lane(lane_id, label, sequential=sequential)
    for node in node_definitions(context, settings):
        monitor.add_node(
            node.node_id,
            node.label,
            node.lane,
            upstream=node.upstream,
            dependencies=dict(node.dependencies),
            enabled=node.enabled,
            stale_after_s=node.stale_after_s,
            stuck_after_s=node.stuck_after_s,
            startup_grace_s=node.startup_grace_s,
            error_window_s=node.error_window_s,
            stale_reason=node.stale_reason,
        )
    return monitor


__all__ = ["NodeDefinition", "build_health_monitor", "derive_stale_after", "node_definitions"]
