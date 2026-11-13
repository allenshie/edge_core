"""BaseIngestionEngine 的健康狀態，回報為背景節點 ingest-worker（見 docs/HEALTH.md「節點」）。

只觀察 worker 的讀取，不影響取流、重連與卡住判定。
"""
from __future__ import annotations

import dataclasses
import threading

from smart_workflow import EngineStatus, EngineStatusCell

_ERROR_REASONS = {"read_failed": "read_failed", "open_failed": "open_failed", "error": "worker_error"}


class IngestionStatusMixin:
    _status_cell: EngineStatusCell
    _status_generation: int | None
    _supervisor: threading.Thread | None
    source_label: str

    def status(self) -> EngineStatus:
        """active：已啟動且尚未 finished／fatal；alive：supervisor 是否存活。

        檔案播完且不 loop 時 active 為 False，supervisor 正常結束不算 background_dead。
        """
        active = self._supervisor is not None and not self._is_terminal()  # type: ignore[attr-defined]
        alive = self._supervisor_alive()  # type: ignore[attr-defined]
        return dataclasses.replace(self._status_cell.get(), active=active, alive=alive)

    def stuck_exit_seconds(self) -> float:
        """讀取卡住多久判定為無法恢復：至少 30 秒，而且要晚於 supervisor 自己的處理
        （開啟逾時 + stall 判定 + 停止 worker 的等待，再加 5 秒），避免換 worker 之前就結束程式。"""
        cfg = self._worker_config  # type: ignore[attr-defined]
        handled_by_supervisor = cfg.open_timeout_seconds + cfg.stall_seconds + cfg.worker_stop_timeout_seconds + 5.0
        return max(30.0, float(handled_by_supervisor))

    def _on_read_begin(self, generation: int) -> None:
        if generation == self._status_generation:
            self._status_cell.mark_busy()

    def _on_read_end(self, generation: int, outcome: str, error: str | None) -> None:
        if generation != self._status_generation:
            return
        reason = _ERROR_REASONS.get(outcome)
        if reason is not None:
            self._status_cell.mark_error(reason, error or f"{self.source_label} {outcome}")
        else:
            # frame：成功在 _on_frame 記錄；eof：來源正常結束，不算錯誤。
            self._status_cell.mark_idle()


__all__ = ["IngestionStatusMixin"]
