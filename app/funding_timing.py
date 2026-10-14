import time, math
from dataclasses import dataclass


@dataclass(frozen=True)
class FundingWindow:
    due: bool
    periods: int
    reason: str


def window(next_ts, hold_seconds, interval_hours=None, now_ms=None):
    if next_ts is None:
        return FundingWindow(False, 0, "UNKNOWN")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if (
        not math.isfinite(float(next_ts))
        or not math.isfinite(float(hold_seconds))
        or hold_seconds < 0
    ):
        return FundingWindow(False, 0, "INVALID")
    ts = int(next_ts)
    if ts < 10_000_000_000:
        ts *= 1000
    if ts < now_ms:
        return FundingWindow(False, 0, "STALE")
    end = now_ms + int(hold_seconds * 1000)
    if ts > end:
        return FundingWindow(False, 0, "NOT_DUE")
    if interval_hours is None:
        return FundingWindow(True, 1, "DUE")
    if not math.isfinite(float(interval_hours)) or interval_hours <= 0:
        return FundingWindow(False, 0, "INVALID")
    interval = int(interval_hours * 3600 * 1000)
    if interval <= 0:
        return FundingWindow(False, 0, "INVALID")
    return FundingWindow(True, 1 + max(0, (end - ts) // interval), "DUE")


def carry_pct(long_rate, short_rate, w):
    if not w.due:
        return 0.0
    return (short_rate - long_rate) * 100 * w.periods
