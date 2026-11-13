"""明確無法恢復的推理錯誤：CUDA context 損壞後每一輪都會失敗，改為結束程式交給 systemd 或 k8s 重啟（見 docs/HEALTH.md「無法恢復時」）。"""
from __future__ import annotations

from smart_workflow import FatalTaskError

# 只放確定會讓 CUDA context 損壞的錯誤；其他錯誤照舊拋出，中斷這一輪並退避。
CUDA_UNRECOVERABLE_MARKERS: tuple[str, ...] = (
    "illegal memory access",
    "unspecified launch failure",
    "device-side assert triggered",
)

CUDA_UNRECOVERABLE_REASON = "cuda_unrecoverable"


def raise_if_cuda_unrecoverable(exc: BaseException) -> None:
    """訊息含有清單中的字串時拋出 FatalTaskError；否則什麼都不做，由呼叫端照原樣拋出。"""
    message = str(exc)
    if any(marker in message for marker in CUDA_UNRECOVERABLE_MARKERS):
        raise FatalTaskError(CUDA_UNRECOVERABLE_REASON, message) from exc


__all__ = ["CUDA_UNRECOVERABLE_MARKERS", "CUDA_UNRECOVERABLE_REASON", "raise_if_cuda_unrecoverable"]
