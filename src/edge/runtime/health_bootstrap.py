"""啟動與停止健康回報：啟動時建立 HealthMonitor、StatusServer 與 HealthRuntime，結束時停止 StatusServer 與 HealthMonitor。

EDGE_HEALTH_ENABLED=0 時什麼都不建立：沒有 HealthMonitor、沒有 StatusServer，
TaskContext.health 維持 smart-workflow 的 no-op，行為和導入前相同。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from smart_health_record import HealthMonitor, StatusServer
from smart_workflow import HealthRuntime, TaskContext

from edge.runtime.health_settings import HealthSettings
from edge.runtime.health_topology import build_health_monitor
from edge.runtime.shutdown_summary import cleanup_record

LOGGER = logging.getLogger(__name__)


def _exit_process(code: int) -> None:
    """判斷為無法恢復時結束程式；獨立成函式，測試可以替換。"""
    os._exit(code)


@dataclass
class HealthBootstrap:
    settings: HealthSettings
    monitor: HealthMonitor | None = None
    status_server: StatusServer | None = None
    health: HealthRuntime | None = None

    def stop(self) -> list[dict[str, Any]]:
        if self.monitor is None:
            return [
                cleanup_record(
                    item="health.monitor",
                    type="thread",
                    state="skipped",
                    ok=True,
                    alive_before=False,
                    alive_after=False,
                    detail="health reporting disabled",
                )
            ]
        records = []
        if self.status_server is not None:
            records.append(_stop("health.status_server", "server", self.status_server.stop))
        records.append(_stop("health.monitor", "thread", self.monitor.stop))
        return records


def _stop(item: str, kind: str, fn) -> dict[str, Any]:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return cleanup_record(item=item, type=kind, state="failed", ok=False, alive_before=True,
                              alive_after=True, detail=f"{item} stop failed", error=str(exc))
    return cleanup_record(item=item, type=kind, state="done", ok=True, alive_before=True,
                          alive_after=False, detail=f"{item} stopped")


def start_health_reporting(context: TaskContext, settings: HealthSettings) -> HealthBootstrap:
    """依設定建立 HealthMonitor、StatusServer 與 HealthRuntime，並放進 context.health。"""
    bootstrap = HealthBootstrap(settings=settings)
    if not settings.enabled:
        LOGGER.info("健康回報已停用（EDGE_HEALTH_ENABLED=0）")
        return bootstrap

    monitor = build_health_monitor(context, settings)
    monitor.start()
    bootstrap.monitor = monitor

    if settings.status_port is not None:
        server = StatusServer(monitor, port=settings.status_port, host=settings.status_host)
        try:
            server.start()
        except OSError as exc:
            LOGGER.error("健康狀態服務無法綁定 %s:%s，程式繼續執行：%s", settings.status_host, settings.status_port, exc)
        else:
            bootstrap.status_server = server
            LOGGER.info("健康狀態服務已啟動：%s:%s", settings.status_host, server.port)

    summary_interval = float(getattr(context.config, "health_report_interval_seconds", 5.0) or 5.0)
    health = HealthRuntime(
        monitor,
        loop_timeout_s=settings.loop_timeout_s,
        summary_interval_s=summary_interval,
        exit_on_unrecoverable=settings.exit_on_failure,
        exit_fn=lambda code: _exit_process(code),
    )
    context.health = health
    bootstrap.health = health
    if not settings.exit_on_failure:
        LOGGER.warning("健康回報為觀察模式（EDGE_HEALTH_EXIT_ON_FAILURE=0）：判斷為無法恢復時不結束程式")
    return bootstrap


__all__ = ["HealthBootstrap", "start_health_reporting"]
