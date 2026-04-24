"""Ingestion source health tracking and evaluation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass
class IngestionHealthTracker:
    source_label: str
    source_health_state: str = "ok"
    read_failure_count: int = 0
    consecutive_read_failures: int = 0
    reconnect_count: int = 0
    last_read_failure_ts: datetime | None = None
    last_read_failure_reason: str | None = None
    last_reconnect_ts: datetime | None = None

    def mark_frame_success(self) -> None:
        self.consecutive_read_failures = 0
        self.source_health_state = "ok"

    def record_read_failure(self, reason: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        self.read_failure_count += 1
        self.consecutive_read_failures += 1
        self.last_read_failure_ts = now
        self.last_read_failure_reason = reason
        self.source_health_state = "degraded"

    def record_reconnect(self) -> None:
        self.reconnect_count += 1
        self.last_reconnect_ts = datetime.now(timezone.utc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "source_label": self.source_label,
            "source_health_state": self.source_health_state,
            "read_failure_count": self.read_failure_count,
            "consecutive_read_failures": self.consecutive_read_failures,
            "reconnect_count": self.reconnect_count,
            "last_read_failure_ts": self.last_read_failure_ts,
            "last_read_failure_reason": self.last_read_failure_reason,
            "last_reconnect_ts": self.last_reconnect_ts,
        }


@dataclass(frozen=True)
class IngestionHealthEvaluation:
    health_state: str
    reason: str | None
    worker_alive: bool
    extra_fields: dict[str, Any]
    snapshot: dict[str, Any]


@dataclass(frozen=True)
class IngestionRecoveryDecision:
    action: str
    reason: str | None
    cooldown_remaining_s: float | None
    extra_fields: dict[str, Any]


class IngestionHealthPolicy:
    @staticmethod
    def evaluate(
        *,
        tracker_snapshot: Mapping[str, Any],
        mode: str | None,
        source_label: str,
        session_id: str,
        frame_seq: int,
        capture_fps: float | None,
        capture_age_seconds: float | None,
        stale_threshold_seconds: float,
        worker_alive: bool,
        capture_ts: datetime | None = None,
    ) -> IngestionHealthEvaluation:
        source_health_state = str(tracker_snapshot.get("source_health_state") or "ok")
        read_failure_count = int(tracker_snapshot.get("read_failure_count") or 0)
        consecutive_read_failures = int(tracker_snapshot.get("consecutive_read_failures") or 0)
        reconnect_count = int(tracker_snapshot.get("reconnect_count") or 0)
        last_read_failure_ts = tracker_snapshot.get("last_read_failure_ts")
        last_read_failure_reason = tracker_snapshot.get("last_read_failure_reason")
        last_reconnect_ts = tracker_snapshot.get("last_reconnect_ts")
        source_issue_age_s = None
        if isinstance(last_read_failure_ts, datetime):
            source_issue_age_s = max(0.0, (datetime.now(timezone.utc) - last_read_failure_ts).total_seconds())

        source_health = "degraded" if source_health_state == "degraded" or consecutive_read_failures > 0 else "ok"
        stale_capture = (
            stale_threshold_seconds > 0
            and capture_age_seconds is not None
            and capture_age_seconds >= stale_threshold_seconds
        )
        health_state = "degraded" if stale_capture else source_health
        reason = "stale_capture" if stale_capture else "source_unhealthy" if source_health == "degraded" else None
        note = (
            f"mode={mode or 'rtsp'} source={source_label} "
            f"source_health={source_health} health_state={health_state} "
            f"reconnects={reconnect_count} read_failures={read_failure_count}"
        )
        extra_fields: dict[str, Any] = {
            "mode": mode,
            "source": source_label,
            "capture_ts": capture_ts,
            "source_health": source_health,
            "read_failures": read_failure_count,
            "consecutive_failures": consecutive_read_failures,
            "reconnect_count": reconnect_count,
            "last_source_issue": last_read_failure_reason,
            "last_source_issue_age_s": source_issue_age_s,
            "last_reconnect_ts": last_reconnect_ts,
        }
        snapshot: dict[str, Any] = {
            "stage": "ingest",
            "state": health_state,
            "session_id": session_id,
            "frame_seq": frame_seq,
            "capture_fps": capture_fps,
            "infer_fps": None,
            "stream_output_fps": None,
            "stream_unique_fps": None,
            "age_s": capture_age_seconds,
            "alive": worker_alive,
            "note": note,
        }
        snapshot.update(extra_fields)
        if capture_ts is not None:
            snapshot["capture_ts"] = capture_ts
        return IngestionHealthEvaluation(
            health_state=health_state,
            reason=reason,
            worker_alive=worker_alive,
            extra_fields=extra_fields,
            snapshot=snapshot,
        )


class IngestionRecoveryPolicy:
    @staticmethod
    def evaluate(
        *,
        evaluation: IngestionHealthEvaluation,
        tracker_snapshot: Mapping[str, Any],
        recovery_cooldown_seconds: float = 30.0,
    ) -> IngestionRecoveryDecision:
        last_reconnect_ts = tracker_snapshot.get("last_reconnect_ts")
        cooldown_remaining_s: float | None = None
        if isinstance(last_reconnect_ts, datetime):
            elapsed_s = max(0.0, (datetime.now(timezone.utc) - last_reconnect_ts).total_seconds())
            cooldown_remaining_s = max(0.0, recovery_cooldown_seconds - elapsed_s)

        if not evaluation.worker_alive:
            return IngestionRecoveryDecision(
                action="restart",
                reason="worker_not_alive",
                cooldown_remaining_s=cooldown_remaining_s,
                extra_fields={
                    "recovery_action": "restart",
                    "recovery_reason": "worker_not_alive",
                    "recovery_cooldown_remaining_s": cooldown_remaining_s,
                },
            )

        should_restart = evaluation.reason in {"stale_capture", "source_unhealthy"}
        reason = evaluation.reason if should_restart else None

        if should_restart and cooldown_remaining_s is not None and cooldown_remaining_s > 0:
            should_restart = False
            reason = "restart_cooldown"

        action = "restart" if should_restart else "none"
        extra_fields = {
            "recovery_action": action,
            "recovery_reason": reason,
            "recovery_cooldown_remaining_s": cooldown_remaining_s,
        }
        return IngestionRecoveryDecision(
            action=action,
            reason=reason,
            cooldown_remaining_s=cooldown_remaining_s,
            extra_fields=extra_fields,
        )
