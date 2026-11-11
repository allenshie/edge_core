"""健康回報的設定（spec-edge.md 第 6 節）。

`config.py` 已經很長，新設定集中在這裡；`EDGE_HEALTH_REPORT_INTERVAL_SEC` 沿用
`EdgeConfig.health_report_interval_seconds`。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_FALSE_VALUES = {"0", "false", "no", "off"}


def _flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in _FALSE_VALUES


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


@dataclass(frozen=True)
class HealthSettings:
    enabled: bool = True                      # EDGE_HEALTH_ENABLED：總開關
    exit_on_failure: bool = True              # EDGE_HEALTH_EXIT_ON_FAILURE：0 為觀察模式
    status_host: str = "127.0.0.1"            # EDGE_HEALTH_STATUS_HOST
    status_port: int | None = None            # EDGE_HEALTH_STATUS_PORT：未設定時不啟動 StatusServer
    stale_after_override: float | None = None  # EDGE_HEALTH_STALE_AFTER_SECONDS
    loop_timeout_s: float = 30.0              # EDGE_HEALTH_LOOP_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "HealthSettings":
        env = os.environ if environ is None else environ
        port = _optional(env, "EDGE_HEALTH_STATUS_PORT")
        stale = _optional(env, "EDGE_HEALTH_STALE_AFTER_SECONDS")
        loop_timeout = _optional(env, "EDGE_HEALTH_LOOP_TIMEOUT_SECONDS")
        return cls(
            enabled=_flag(env, "EDGE_HEALTH_ENABLED", True),
            exit_on_failure=_flag(env, "EDGE_HEALTH_EXIT_ON_FAILURE", True),
            status_host=_optional(env, "EDGE_HEALTH_STATUS_HOST") or "127.0.0.1",
            status_port=int(port) if port is not None else None,
            stale_after_override=float(stale) if stale is not None else None,
            loop_timeout_s=float(loop_timeout) if loop_timeout is not None else 30.0,
        )


__all__ = ["HealthSettings"]
