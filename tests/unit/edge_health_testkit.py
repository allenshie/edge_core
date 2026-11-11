"""健康回報測試共用的假元件（不連接相機、ffmpeg 或 broker）。"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from smart_workflow import TaskContext


class Monitor:
    def report_event(self, *args: Any, **kwargs: Any) -> None:
        return None

    def heartbeat(self, **kwargs: Any) -> None:
        return None


def streaming_config(*, enabled: bool = True, url: str = "rtmp://localhost/test") -> SimpleNamespace:
    return SimpleNamespace(
        camera=SimpleNamespace(camera_id="cam-1"),
        streaming=SimpleNamespace(
            enabled=enabled,
            fps=15,
            url=url,
            strategy="cpu",
            idle_timeout_seconds=3.0,
            restart_backoff_seconds=1.0,
            recording=SimpleNamespace(enabled=False),
            output_width=None,
            output_height=None,
        ),
        ingestion=SimpleNamespace(
            mode="file",
            file=SimpleNamespace(fps=15),
            camera=SimpleNamespace(fps=15),
            rtsp=SimpleNamespace(fps=15),
        ),
        overlay=SimpleNamespace(show_track_info=False, show_score_info=False),
        matching_result=SimpleNamespace(enabled=False, resource_name="matching_result_snapshot"),
        health_report_interval_seconds=5.0,
        health_stale_threshold_seconds=5.0,
    )


def context(config: Any) -> TaskContext:
    return TaskContext(logger=logging.getLogger("test"), config=config, monitor=Monitor())


class FakeFfmpeg:
    """寫入結果可切換的假 ffmpeg。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.writes = 0
        self.generation = 0
        self.restart_calls = 0
        self.on_write = None

    def is_alive(self) -> bool:
        return True

    def close_async(self, *, reason: str) -> bool:
        self.generation += 1
        return True

    def close(self) -> None:
        return None

    def lifecycle_generation(self) -> int:
        return self.generation

    def restart(self, *, expected_generation: int | None = None) -> bool:
        self.restart_calls += 1
        return True

    def write_frame(self, frame: Any) -> None:
        self.writes += 1
        if self.on_write is not None:
            self.on_write()
        if self.fail:
            raise RuntimeError("broken pipe")
