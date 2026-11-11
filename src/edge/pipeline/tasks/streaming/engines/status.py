"""BaseStreamingEngine 的健康狀態（stream-output，spec-edge.md 2、3.2）。

只觀察輸出執行緒寫入輸出的呼叫，不影響推流內容、節拍與重啟。
"""
from __future__ import annotations

import dataclasses
import threading

from smart_workflow import EngineStatus, EngineStatusCell


class StreamingStatusMixin:
    _status_cell: EngineStatusCell
    _last_write_ok: bool | None
    _desired_streaming: bool
    _output_thread: threading.Thread | None

    def _init_status(self) -> None:
        self._status_cell = EngineStatusCell()
        self._last_write_ok = None

    def status(self) -> EngineStatus:
        """active：目前該推流；alive：輸出執行緒是否存活。"""
        thread = self._output_thread
        alive = thread is not None and thread.is_alive()
        return dataclasses.replace(self._status_cell.get(), active=bool(self._desired_streaming), alive=alive)

    def sink_connected(self) -> bool | None:
        """推流目的地是否可用；不該推流、沒有畫面、還沒寫入過時回傳 None（無法判斷）。"""
        if not self._desired_streaming or self._get_latest_packet() is None:  # type: ignore[attr-defined]
            return None
        return self._last_write_ok

    def _status_write_begin(self) -> None:
        self._status_cell.mark_busy()

    def _status_write_done(self, *, counted: bool) -> None:
        """寫入完成；counted 為 False 代表寫入時已不在推流時段，不算一次輸出。"""
        if counted:
            self._last_write_ok = True
            self._status_cell.mark_success()
        else:
            self._status_cell.mark_idle()

    def _status_write_failed(self, message: str, *, counted: bool) -> None:
        if counted:
            self._last_write_ok = False
            self._status_cell.mark_error("write_failed", message)
        else:
            self._status_cell.mark_idle()


__all__ = ["StreamingStatusMixin"]
