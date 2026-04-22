"""Shared-memory streaming engine specialization."""
from __future__ import annotations

import logging
import os
import gc
import time
from multiprocessing.shared_memory import SharedMemory
from typing import Any, Sequence

import numpy as np
from smart_workflow import TaskContext

from edge.runtime.shutdown_summary import cleanup_record
from edge.schema import EdgeDetection, FrameMeta

from .default import DefaultStreamingEngine
from ..types import StreamPacket, StreamingStatus

LOGGER = logging.getLogger(__name__)


class ShmStreamingEngine(DefaultStreamingEngine):
    """
    極速版優化：
    1. 使用固定雙緩衝視圖 (Static Dual Views)，減少 MMAP 重建開銷。
    2. 將最新 raw frame 放進 SHM，輸出時再複製一份做繪圖，避免重複幀輸出時污染來源視圖。
    3. 僅保留單一輸出 thread，不再經由 worker queue。
    """

    def __init__(self, context: TaskContext | None = None) -> None:
        cam_id = context.config.camera.camera_id if context else "default"
        shm_mb = int(os.environ.get("EDGE_STREAMING_SHM_MB", "30"))
        self._shm_size = shm_mb * 1024 * 1024

        self._shm_names = [f"edge_shm_{cam_id}_0", f"edge_shm_{cam_id}_1"]
        self._shm_writers: list[SharedMemory] = []
        self._write_idx = 0

        for name in self._shm_names:
            try:
                old_shm = SharedMemory(name=name)
                old_shm.close()
                old_shm.unlink()
            except Exception:
                pass
            self._shm_writers.append(SharedMemory(name=name, create=True, size=self._shm_size))

        super().__init__(context, start_output_loop=False)

        # Producer 的 SHM 視圖快取；單一輸出 thread 直接讀這個 cache，不再透過 queue/worker。
        self._writer_views: list[np.ndarray | None] = [None, None]
        self._shm_readers: dict[str, SharedMemory] = {}

        LOGGER.info("[FAST_SHM] Optimized dual buffering initialized. target_fps=%.2f", self._target_fps)
        self._start_output_loop()

    def push(
        self,
        frame: Any,
        detections: Sequence[EdgeDetection],
        phase: str,
        frame_meta: FrameMeta | None = None,
    ) -> StreamingStatus:
        if frame is not None and self._enabled:
            try:
                # 這裡只把最新 raw frame 寫進 SHM；真正的節拍輸出交給單一 output thread。
                if self._writer_views[self._write_idx] is None:
                    self._writer_views[self._write_idx] = np.ndarray(
                        frame.shape,
                        dtype=frame.dtype,
                        buffer=self._shm_writers[self._write_idx].buf,
                    )

                np.copyto(self._writer_views[self._write_idx], frame)

                shm_metadata = {
                    "shm_name": self._shm_names[self._write_idx],
                    "shape": frame.shape,
                    "dtype": str(frame.dtype),
                }

                self._write_idx = (self._write_idx + 1) % 2
                return super().push(shm_metadata, detections, phase, frame_meta=frame_meta)

            except Exception as exc:
                LOGGER.error("FAST_SHM push failed: %s", exc)

        return super().push(frame, detections, phase, frame_meta=frame_meta)

    def _prepare_output_frame(self, packet: StreamPacket) -> Any | None:
        shm_info = packet.frame
        if not isinstance(shm_info, dict) or "shm_name" not in shm_info:
            return None

        try:
            shm_name = shm_info["shm_name"]

            if shm_name not in self._shm_readers:
                self._shm_readers[shm_name] = SharedMemory(name=shm_name, create=False)
                LOGGER.info("Reader established for %s", shm_name)

            reader = self._shm_readers[shm_name]
            frame_view = np.ndarray(shm_info["shape"], dtype=shm_info["dtype"], buffer=reader.buf)

            # 輸出時一定先 copy，避免重複幀輸出時把 SHM source view 畫壞。
            vis_frame = frame_view.copy()
            self._draw_detections(vis_frame, packet.detections)
            return vis_frame

        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            LOGGER.warning("FAST_SHM process failed: %s", exc)
            return None

    def close(self) -> list[dict[str, Any]]:
        records = super().close()
        started = time.perf_counter()
        writer_views_alive_before = any(view is not None for view in self._writer_views)
        # 先釋放 writer views，避免 numpy view 還持有 SHM buffer 時就 close/unlink。
        self._writer_views = [None, None]
        self._write_idx = 0
        self._clear_latest_packet()
        for reader in self._shm_readers.values():
            try:
                reader.close()
            except Exception:
                pass
        self._shm_readers.clear()
        for shm in self._shm_writers:
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass
        self._shm_writers.clear()
        gc.collect()
        duration_ms = (time.perf_counter() - started) * 1000.0
        records.append(
            cleanup_record(
                item="streaming.shm",
                type="resource",
                state="done" if writer_views_alive_before else "skipped",
                ok=True,
                alive_before=writer_views_alive_before,
                alive_after=False,
                duration_ms=duration_ms,
                detail="shared memory buffers released",
            )
        )
        LOGGER.info(
            "[FAST_SHM] Final Stats - processed=%d dropped=%d reconnect=%d",
            self._processed_frames,
            self._dropped_frames,
            self._reconnect_count,
        )
        return records
