from dataclasses import dataclass


@dataclass(frozen=True)
class ResumeEvidence:
    safe: bool
    reasons: tuple


def collect(integrity, private_ready, unknown_orders, heartbeat, operator_stopped):
    reasons = []
    if operator_stopped:
        reasons.append("OPERATOR_STOPPED")
    if not integrity.safe:
        reasons.append(integrity.reason)
    if not private_ready:
        reasons.append("PRIVATE_NOT_READY")
    if unknown_orders:
        reasons.append("UNKNOWN_ORDERS")
    if not heartbeat.healthy:
        reasons.append(heartbeat.reason)
    return ResumeEvidence(not reasons, tuple(dict.fromkeys(reasons)))


def collect_current(supervisor, summary, now, max_age=15):
    """A past successful reconciliation is not a current resume permission."""
    import math
    from types import SimpleNamespace
    from .live_heartbeat import Heartbeat

    summary = summary or {}
    stamp = summary.get("ts")
    fresh = (
        not isinstance(stamp, bool)
        and isinstance(stamp, (int, float))
        and math.isfinite(stamp)
        and 0 <= now - stamp <= max_age
    )
    known = (
        type(summary.get("unknown_orders")) is int and summary["unknown_orders"] == 0
    )
    return collect(
        SimpleNamespace(
            safe=supervisor.restart_clean and summary.get("reconciled") is True,
            reason="RESTART_UNSAFE",
        ),
        supervisor.private_verified and summary.get("private_verified") is True,
        supervisor.unknown_orders or not known,
        Heartbeat(fresh, "PRIVATE_SNAPSHOT_STALE" if not fresh else "OK"),
        False,
    )
