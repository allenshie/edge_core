"""spec-edge.md 第 8 節整合測試：用假的 Engine 跑 run_daemon（合併後的 runner、HealthMonitor、回報執行緒），
驗證節點燈號與 exit_fn 的呼叫時機。不需要 GPU、相機或 broker。
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "regression"))

import business_fakes  # noqa: E402
import harness  # noqa: E402
from edge.pipeline.tasks.ingestion import task as ingestion_task  # noqa: E402
from edge.pipeline.tasks.ingestion.engines import BaseIngestionEngine  # noqa: E402
from edge.runtime import health_bootstrap  # noqa: E402

TIMELINE: dict[str, Any] = {}


class ScenarioEngine(BaseIngestionEngine):
    """正常送畫面 → 畫面停住 → 背景執行緒 fatal；在各階段記錄 /status 的燈號。"""

    source_label = "scenario"

    def __init__(self, context: Any = None, **kwargs: Any) -> None:
        super().__init__(context, **kwargs)
        self._context = context
        self._seq = 0
        self._phase_started = time.monotonic()
        self._phase = "flowing"

    def status(self):
        return dataclasses.replace(self._status_cell.get(), active=True, alive=True)

    def _nodes(self) -> dict:
        return self._context.health.monitor.status()["nodes"]

    def _switch(self, phase: str) -> None:
        self._phase = phase
        self._phase_started = time.monotonic()

    def fetch(self) -> dict[str, Any]:
        time.sleep(0.02)
        # 一定會結束：沒有啟用健康回報，或超過 15 秒都還沒走完情境。
        if self._context.health.monitor is None or time.monotonic() - TIMELINE["started"] > 15.0:
            TIMELINE["aborted"] = True
            raise KeyboardInterrupt
        elapsed = time.monotonic() - self._phase_started
        if self._phase == "flowing":
            self._seq += 1
            self._status_cell.mark_success()
            # 背景節點要兩次回報（建立基準、轉送成功）加一次評估才會是 ok，所以等 4 秒。
            if elapsed > 4.0:
                TIMELINE["flowing"] = self._nodes()
                self._switch("frozen")
        elif self._phase == "frozen" and elapsed > 3.0:
            TIMELINE["frozen"] = self._nodes()
            self._status_cell.mark_fatal("ingest_workers_stuck", "3 個 capture worker 無法停止")
            TIMELINE["fatal_at"] = time.monotonic()
            self._switch("fatal")
        elif self._phase == "fatal" and (TIMELINE["exit_calls"] or elapsed > 4.0):
            raise KeyboardInterrupt
        return {
            "frame": np.full((4, 6, 3), 10, dtype=np.uint8),
            "source": self.source_label,
            "capture_ts": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "frame_seq": max(self._seq, 1),
            "capture_monotonic": time.monotonic(),
        }


def _run(overrides: dict[str, str]) -> dict[str, Any]:
    TIMELINE.clear()
    TIMELINE["exit_calls"] = []
    TIMELINE["started"] = time.monotonic()

    def record_exit(code: int) -> None:
        TIMELINE["exit_calls"].append((code, time.monotonic()))

    env = harness._build_env({"EDGE_HEALTH_STALE_AFTER_SECONDS": "1", **overrides})
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch.dict(ingestion_task.ENGINE_BY_MODE, {"file": ScenarioEngine}), \
            mock.patch.object(harness.importlib.import_module("edge.messaging"), "MessagingClient",
                              business_fakes.FakeMessagingClient), \
            mock.patch.object(health_bootstrap, "_exit_process", record_exit):
        business_fakes.reset_recorder()
        from edge.config import load_config
        from edge.main import run_daemon

        run_daemon(load_config())
    return dict(TIMELINE)


def test_status_lights_and_exit_timing() -> None:
    result = _run({"EDGE_HEALTH_ENABLED": "1"})

    assert "aborted" not in result
    flowing = result["flowing"]
    for node in ("ingest-worker", "ingest", "infer", "publish"):
        assert flowing[node]["state"] == "ok", (node, flowing[node])
    assert flowing["stream"]["state"] == "disabled"
    assert flowing["stream-output"]["state"] == "disabled"

    frozen = result["frozen"]
    assert frozen["ingest"]["state"] == "unavailable"
    assert frozen["ingest"]["reason"] == "stale_success"
    assert frozen["ingest-worker"]["state"] == "ok"  # 背景執行緒仍在讀（假 Engine 只停住新畫面）

    assert len(result["exit_calls"]) == 1
    code, exited_at = result["exit_calls"][0]
    assert code == 70
    assert 0 <= exited_at - result["fatal_at"] <= 2.5


def test_observation_mode_never_calls_exit() -> None:
    result = _run({"EDGE_HEALTH_ENABLED": "1", "EDGE_HEALTH_EXIT_ON_FAILURE": "0"})

    assert result["exit_calls"] == []
    assert result["frozen"]["ingest"]["state"] == "unavailable"


def test_disabled_builds_no_monitor_and_skips_warmup() -> None:
    from edge.pipeline.tasks.inference.scheduled_engine import engine as scheduled_engine

    def fail(*args, **kwargs):
        raise AssertionError("EDGE_HEALTH_ENABLED=0 時不能建立 HealthMonitor 或暖機")

    with mock.patch.object(health_bootstrap, "build_health_monitor", fail), \
            mock.patch.object(scheduled_engine, "run_model_warmups", fail):
        result = harness.run_harness({"EDGE_HEALTH_ENABLED": "0"})

    assert result["_exit_calls"] == []
    assert harness.comparable(result) == harness.load_baseline()


def test_enabled_warms_up_every_scheduled_model() -> None:
    from edge.pipeline.tasks.inference.scheduled_engine import engine as scheduled_engine

    seen: list[list] = []
    real = scheduled_engine.run_model_warmups

    def spy(entries, **kwargs):
        entries = list(entries)
        seen.append([(phase, name) for phase, name, _ in entries])
        return real(entries, **kwargs)

    with mock.patch.object(scheduled_engine, "run_model_warmups", spy):
        harness.run_harness({"EDGE_HEALTH_ENABLED": "1"})

    assert seen == [[("alpha", "detector"), ("alpha", "classifier"), ("beta", "detector")]]
