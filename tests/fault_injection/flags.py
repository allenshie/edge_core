"""旗標檔：`D1_FAULT_DIR/<名稱>` 存在代表要製造對應的故障。"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

LOGGER = logging.getLogger("fault_injection")

POLL_SECONDS = 0.2


def fault_dir() -> Path:
    return Path(os.environ.get("D1_FAULT_DIR", "/tmp/d1cam"))


def is_set(name: str) -> bool:
    return (fault_dir() / name).exists()


def block_while(name: str) -> None:
    """旗標存在時一直阻塞（不理會 abort），旗標刪除後返回。"""
    if not is_set(name):
        return
    LOGGER.warning("D1 fault %s：開始阻塞", name)
    while is_set(name):
        time.sleep(POLL_SECONDS)
    LOGGER.warning("D1 fault %s：旗標已刪除，解除阻塞", name)
