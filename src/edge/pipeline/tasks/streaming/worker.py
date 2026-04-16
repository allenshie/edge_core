"""Background worker for streaming packets."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from .types import StreamPacket

LOGGER = logging.getLogger(__name__)


class StreamingWorker:
    """Consumes packets from queue in background."""

    def __init__(
        self,
        packet_queue: queue.Queue,
        stop_event: threading.Event,
        process_packet: Callable[[StreamPacket], None],
    ) -> None:
        self._queue = packet_queue
        self._stop_event = stop_event
        self._process_packet = process_packet
        self._shm_readers: dict[str, Any] = {}  # 資源槽位：供特定的 Engine 實現多緩衝讀取
        self._thread = threading.Thread(
            target=self._loop,
            name="EdgeStreamingWorker",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        for name, reader in self._shm_readers.items():
            try:
                reader.close()
                LOGGER.info("Streaming worker SHM reader [%s] closed", name)
            except Exception as exc:
                LOGGER.warning("Failed to close SHM reader [%s] in worker: %s", name, exc)
        self._shm_readers.clear()
        if self._thread.is_alive():
            LOGGER.warning("streaming worker still alive after %.1fs timeout", timeout)

    def _loop(self) -> None:
        LOGGER.info("streaming worker started")
        while not self._stop_event.is_set():
            try:
                packet: StreamPacket = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process_packet(packet)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("streaming worker packet handling failed: %s", exc)
            finally:
                self._queue.task_done()
        LOGGER.info("streaming worker stopped")
