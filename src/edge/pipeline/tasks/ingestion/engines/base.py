"""Common ingestion engine routines: supervise one capture worker per connection."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from smart_workflow import TaskContext, TaskError

from edge.config import IngestionWorkerConfig
from edge.pipeline.tasks.ingestion.backoff import ReconnectBackoff
from edge.pipeline.tasks.ingestion.health import IngestionHealthTracker
from edge.pipeline.tasks.ingestion.sources import FrameSource, SourceFrame
from edge.pipeline.tasks.ingestion.worker import CaptureWorker
from edge.runtime.rate_meter import RateMeter
from edge.runtime.shutdown_summary import cleanup_record

LOGGER = logging.getLogger(__name__)


class BaseIngestionEngine:
    """Supervisor：每次連線建立一個 CaptureWorker，負責換 worker、退避重連與卡住判定。

    恢復責任完全在 engine 內；呼叫端只透過 fetch() / health_snapshot() 觀察。
    """

    source_label = "ingestion"
    _first_frame_timeout_seconds = 5.0

    def __init__(self, context: TaskContext | None = None, *, worker_config: IngestionWorkerConfig | None = None) -> None:
        ingestion = getattr(getattr(context, "config", None), "ingestion", None)
        self._worker_config = worker_config or getattr(ingestion, "worker", None) or IngestionWorkerConfig()
        self._cached_config: Any = None
        self._start_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._frame_ready = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._supervisor_stop: threading.Event | None = None
        self._worker: CaptureWorker | None = None
        self._generation = 0
        self._reconnect_pending = False
        self._stuck_workers: list[CaptureWorker] = []
        self._fatal_reason: str | None = None
        self._finished_reason: str | None = None
        self._reconnect_backoff_s = 0.0
        self._latest_frame: Any = None
        self._latest_capture_ts: datetime | None = None
        self._latest_frame_seq: int = 0
        self._last_error: str | None = None
        self._health = IngestionHealthTracker(source_label=self.source_label)
        self._capture_rate = RateMeter()

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        with self._start_lock:
            if self._is_terminal() or self._supervisor_alive():
                return
            config = self._cached_config
            if config is None:
                raise TaskError(f"{self.source_label} 設定尚未初始化")
            self._validate_config(config)
            LOGGER.info("starting %s ingestion supervisor", self.source_label)
            # 每次 start 都用新的 stop event，舊 supervisor 收到的停止訊號不會被取消。
            stop_event = threading.Event()
            self._supervisor_stop = stop_event
            self._frame_ready.clear()
            self._supervisor = threading.Thread(
                target=self._supervise,
                name=f"{self.source_label.capitalize()}IngestionSupervisor",
                args=(config, stop_event),
                daemon=True,
            )
            self._supervisor.start()

    def stop(self) -> list[dict[str, Any]]:
        alive_before = self.is_started()
        started = time.perf_counter()
        with self._start_lock:
            stop_event, supervisor, worker = self._supervisor_stop, self._supervisor, self._worker
        if stop_event is not None:
            stop_event.set()
        timeout = self._worker_config.worker_stop_timeout_seconds
        if worker is not None:
            worker.request_stop()
            worker.join(timeout)
        if supervisor is not None and supervisor is not threading.current_thread():
            supervisor.join(timeout=timeout + self._poll_seconds())
        alive_after = (supervisor is not None and supervisor.is_alive()) or (worker is not None and worker.is_alive())
        if alive_before and alive_after:
            LOGGER.warning("%s ingestion capture worker still alive after stop", self.source_label)
        duration_ms = (time.perf_counter() - started) * 1000.0
        if not alive_before:
            return [
                cleanup_record(
                    item=f"{self.source_label}.capture",
                    type="thread",
                    state="skipped",
                    ok=True,
                    alive_before=False,
                    alive_after=False,
                    duration_ms=duration_ms,
                    detail="capture thread not running",
                )
            ]
        state = "done" if not alive_after else "timeout"
        detail = "capture thread joined" if not alive_after else "capture thread still alive after stop"
        return [
            cleanup_record(
                item=f"{self.source_label}.capture",
                type="thread",
                state=state,
                ok=not alive_after,
                alive_before=alive_before,
                alive_after=alive_after,
                duration_ms=duration_ms,
                detail=detail,
            )
        ]

    def close(self) -> list[dict[str, Any]]:
        return self.stop()

    def begin_shutdown(self) -> None:
        stop_event, worker = self._supervisor_stop, self._worker
        if stop_event is not None:
            stop_event.set()
        if worker is not None:
            worker.request_stop()

    def is_started(self) -> bool:
        """Ingestion 是否仍在運作：supervisor 存活且尚未 fatal（重連退避期間也算運作中）。"""
        return self._supervisor_alive() and self._fatal_reason is None

    # ------------------------------------------------------------------ observation

    def health_snapshot(self) -> dict[str, Any]:
        _, capture_ts, frame_seq = self._snapshot_latest_frame()
        return {
            "worker_alive": self.is_started(),
            "frame_seq": frame_seq,
            "capture_ts": capture_ts,
            "last_error": self._last_error,
            "worker_generation": self._generation,
            "stuck_workers": self._count_stuck_workers(),
            "fatal": self._fatal_reason is not None,
            "fatal_reason": self._fatal_reason,
            "finished": self._finished_reason is not None,
            "reconnect_backoff_s": self._reconnect_backoff_s,
            **self._health.snapshot(),
        }

    @property
    def capture_rate_meter(self) -> RateMeter:
        return self._capture_rate

    def fetch(self) -> dict[str, Any]:
        self.start()
        if not self._wait_for_frame():
            detail = f"：{self._last_error}" if self._last_error else ""
            raise TaskError(f"{self.source_label} 無法取得影格{detail}")
        frame, capture_ts, frame_seq = self._snapshot_latest_frame()
        if frame is None:
            raise TaskError(f"{self.source_label} 無法取得影格")
        if capture_ts is None:
            capture_ts = datetime.now(timezone.utc)
        return {
            "frame": frame,
            "source": self.source_label,
            "capture_ts": capture_ts,
            "frame_seq": frame_seq,
        }

    # ------------------------------------------------------------------ subclass hooks

    def _build_source(self, config: Any) -> FrameSource:
        raise NotImplementedError

    def _validate_config(self, config: Any) -> None:
        _ = config

    def _get_drop_frames(self, config: Any) -> int:
        _ = config
        return 0

    def _get_capture_interval_seconds(self, config: Any, cycle_start: float) -> float:
        _ = config
        _ = cycle_start
        return 0.0

    def _reconnect_base_seconds(self, config: Any) -> float:
        _ = config
        return 1.0

    def _should_reconnect(self, exit_reason: str) -> bool:
        """worker 結束後是否要重連；回傳 False 時 engine 進入 finished。"""
        _ = exit_reason
        return True

    # ------------------------------------------------------------------ supervisor

    def _supervise(self, config: Any, stop_event: threading.Event) -> None:
        backoff = ReconnectBackoff(self._reconnect_base_seconds(config), self._worker_config.reconnect_max_seconds)
        worker: CaptureWorker | None = None
        try:
            while not stop_event.is_set():
                worker = self._spawn_worker(config)
                self._watch_worker(worker, stop_event)
                if stop_event.is_set() or self._fatal_reason is not None:
                    return
                reason = "stalled" if worker.stalled else worker.exit_reason or "unknown"
                self._last_error = worker.error or f"{self.source_label} worker exited: {reason}"
                if worker.frames_delivered:
                    backoff.reset()
                if not self._should_reconnect(reason):
                    self._finished_reason = self._last_error
                    LOGGER.warning("%s ingestion finished: %s", self.source_label, self._last_error)
                    return
                self._health.record_read_failure(f"{self.source_label} {reason}: {self._last_error}")
                delay = backoff.next_delay()
                self._reconnect_backoff_s = delay
                LOGGER.warning(
                    "%s 結束（%s：%s），%.2f 秒後重新連線",
                    worker.name,
                    reason,
                    self._last_error,
                    delay,
                )
                if stop_event.wait(timeout=delay):
                    return
        finally:
            if worker is not None and worker.is_alive() and stop_event.is_set():
                worker.request_stop()
                worker.join(self._worker_config.worker_stop_timeout_seconds)

    def _spawn_worker(self, config: Any) -> CaptureWorker:
        with self._frame_lock:
            self._generation += 1
            generation = self._generation
            self._reconnect_pending = generation > 1
        worker = CaptureWorker(
            generation=generation,
            source=self._build_source(config),
            on_frame=self._on_frame,
            frames_per_cycle=max(self._get_drop_frames(config), 0) + 1,
            interval_fn=lambda cycle_start: self._get_capture_interval_seconds(config, cycle_start),
            name=f"{self.source_label.capitalize()}IngestionWorker-{generation}",
        )
        self._worker = worker
        worker.start()
        return worker

    def _watch_worker(self, worker: CaptureWorker, stop_event: threading.Event) -> None:
        cfg = self._worker_config
        while not stop_event.wait(timeout=self._poll_seconds()):
            if not worker.is_alive():
                return
            if worker.last_read_monotonic is None:
                last, limit = worker.started_monotonic or time.monotonic(), cfg.open_timeout_seconds + cfg.stall_seconds
            else:
                last, limit = worker.last_read_monotonic, cfg.stall_seconds
            if time.monotonic() - last > limit:
                worker.stalled = True
                self._retire_worker(worker)
                return

    def _retire_worker(self, worker: CaptureWorker) -> None:
        LOGGER.warning("%s 超過 %.1f 秒沒有新畫面，停止並更換 worker", worker.name, self._worker_config.stall_seconds)
        worker.request_stop()
        if worker.join(self._worker_config.worker_stop_timeout_seconds):
            return
        self._stuck_workers.append(worker)
        stuck = self._count_stuck_workers()
        LOGGER.error("%s 無法停止，列為卡住的 worker（目前 %d 個）", worker.name, stuck)
        if stuck >= self._worker_config.max_stuck_workers:
            self._fatal_reason = f"{stuck} 個 capture worker 無法停止"
            LOGGER.critical("%s ingestion fatal: %s", self.source_label, self._fatal_reason)

    def _count_stuck_workers(self) -> int:
        self._stuck_workers = [worker for worker in self._stuck_workers if worker.is_alive()]
        return len(self._stuck_workers)

    def _poll_seconds(self) -> float:
        return min(0.5, max(self._worker_config.stall_seconds / 4.0, 0.01))

    # ------------------------------------------------------------------ frame slot

    def _on_frame(self, generation: int, frame: SourceFrame, capture_ts: datetime) -> None:
        with self._frame_lock:
            # 只接受目前世代的 worker，舊 worker 晚醒來也無法覆蓋新資料。
            if generation != self._generation:
                return
            if self._reconnect_pending:
                self._reconnect_pending = False
                self._health.record_reconnect()
                LOGGER.info("%s 重新連線成功（worker #%d）", self.source_label, generation)
            self._latest_frame_seq += 1
            self._latest_frame = frame.image
            self._latest_capture_ts = capture_ts
            self._last_error = None
            self._reconnect_backoff_s = 0.0
            self._health.mark_frame_success()
            self._frame_ready.set()
            self._capture_rate.mark(frame_seq=self._latest_frame_seq, ts=capture_ts)

    def _snapshot_latest_frame(self) -> tuple[Any, datetime | None, int]:
        with self._frame_lock:
            return self._latest_frame, self._latest_capture_ts, self._latest_frame_seq

    # ------------------------------------------------------------------ helpers

    def _supervisor_alive(self) -> bool:
        supervisor = self._supervisor
        return supervisor is not None and supervisor.is_alive()

    def _is_terminal(self) -> bool:
        return self._fatal_reason is not None or self._finished_reason is not None

    def _wait_for_frame(self) -> bool:
        """等待畫面；engine 進入 fatal / finished 時立即拋出 TaskError，不必等到逾時。

        來源在背景開啟，所以還沒收到任何畫面時，等待時間要再加上 open timeout。
        """
        timeout = self._first_frame_timeout_seconds
        if not self._frame_ready.is_set():
            timeout += self._worker_config.open_timeout_seconds
        deadline = time.monotonic() + timeout
        while True:
            self._raise_if_terminal()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._frame_ready.is_set()
            if self._frame_ready.wait(timeout=min(remaining, 0.05)):
                self._raise_if_terminal()
                return True

    def _raise_if_terminal(self) -> None:
        if self._fatal_reason is not None:
            raise TaskError(f"{self.source_label} ingestion fatal：{self._fatal_reason}")
        if self._finished_reason is not None:
            raise TaskError(self._finished_reason)

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:  # noqa: BLE001 - 解構時不能拋出例外
            pass
