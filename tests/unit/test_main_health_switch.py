"""健康回報的總開關、觀察模式與 StatusServer。"""
from __future__ import annotations

import json
import logging
import socket
import urllib.request

import pytest
from smart_workflow import HealthRuntime, TaskContext

from edge.runtime import health_bootstrap
from edge.runtime.health_bootstrap import start_health_reporting
from edge.runtime.health_settings import HealthSettings

from edge_health_testkit import Monitor
from test_health_topology import _config


def _context() -> TaskContext:
    return TaskContext(logger=logging.getLogger("test"), config=_config(), monitor=Monitor())


@pytest.fixture
def started():
    items = []
    yield items
    for bootstrap in items:
        bootstrap.stop()


def test_disabled_creates_nothing(monkeypatch) -> None:
    built: list[object] = []
    monkeypatch.setattr(health_bootstrap, "build_health_monitor", lambda *a, **k: built.append(a))
    context = _context()

    bootstrap = start_health_reporting(context, HealthSettings(enabled=False, status_port=0))

    assert built == []
    assert bootstrap.monitor is None and bootstrap.status_server is None and bootstrap.health is None
    assert isinstance(context.health, HealthRuntime) and context.health.enabled is False
    assert [r["state"] for r in bootstrap.stop()] == ["skipped"]


def test_enabled_starts_monitor_and_sets_context_health(started) -> None:
    context = _context()
    context.config.health_report_interval_seconds = 7.0

    bootstrap = start_health_reporting(context, HealthSettings(loop_timeout_s=45.0))
    started.append(bootstrap)

    assert context.health is bootstrap.health
    assert context.health.enabled is True
    assert context.health.monitor is bootstrap.monitor
    assert context.health.loop_timeout_s == 45.0
    assert bootstrap.monitor.topology()["instance"] == "cam07"
    assert bootstrap.status_server is None


@pytest.mark.parametrize(("exit_on_failure", "expected"), [(True, True), (False, False)])
def test_exit_on_failure_is_passed_to_health_runtime(monkeypatch, started, exit_on_failure, expected) -> None:
    captured: dict = {}
    real = health_bootstrap.HealthRuntime

    def spy(monitor, **kwargs):
        captured.update(kwargs)
        return real(monitor, **kwargs)

    monkeypatch.setattr(health_bootstrap, "HealthRuntime", spy)
    context = _context()

    started.append(start_health_reporting(context, HealthSettings(exit_on_failure=exit_on_failure)))

    assert captured["exit_on_unrecoverable"] is expected
    assert captured["summary_interval_s"] == 5.0


def test_exit_fn_calls_replaceable_exit_process(monkeypatch, started) -> None:
    codes: list[int] = []
    monkeypatch.setattr(health_bootstrap, "_exit_process", codes.append)
    context = _context()
    started.append(start_health_reporting(context, HealthSettings()))

    context.health._exit_fn(70)

    assert codes == [70]


def test_status_server_serves_status_when_port_set(started) -> None:
    bootstrap = start_health_reporting(_context(), HealthSettings(status_port=0, status_host="127.0.0.1"))
    started.append(bootstrap)

    with urllib.request.urlopen(f"http://127.0.0.1:{bootstrap.status_server.port}/status", timeout=2) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    assert body["service"] == "edge" and body["instance"] == "cam07"


def test_status_server_bind_failure_logs_error_and_continues(started, caplog) -> None:
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with caplog.at_level(logging.ERROR):
            bootstrap = start_health_reporting(_context(), HealthSettings(status_port=port))
        started.append(bootstrap)
    finally:
        blocker.close()

    assert bootstrap.status_server is None
    assert bootstrap.health is not None
    assert any(r.levelno == logging.ERROR and str(port) in r.getMessage() for r in caplog.records)


def test_stop_stops_server_and_monitor() -> None:
    bootstrap = start_health_reporting(_context(), HealthSettings(status_port=0))

    records = bootstrap.stop()

    assert [(r["item"], r["state"]) for r in records] == [("health.status_server", "done"), ("health.monitor", "done")]
