from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Freshness:
    ok: bool
    reason: str
    age_ms: float


def check(now_ts, book_ts, max_age_ms=1500):
    if (
        any(
            isinstance(x, bool)
            or not isinstance(x, (int, float))
            or not math.isfinite(x)
            for x in (now_ts, book_ts, max_age_ms)
        )
        or max_age_ms <= 0
        or book_ts > now_ts
    ):
        return Freshness(False, "MARKET_DATA_TIME_INVALID", float("inf"))
    age = (now_ts - book_ts) * 1000
    return Freshness(
        age <= max_age_ms, "OK" if age <= max_age_ms else "MARKET_DATA_STALE", age
    )
