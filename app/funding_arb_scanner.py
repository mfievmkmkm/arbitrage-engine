import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FundingArb:
    allowed: bool
    long_venue: str
    short_venue: str
    carry_pct: float
    reason: str
    gross_carry_pct: float = 0


def evaluate(a, b, rate_a, rate_b, interval_a, interval_b, fees_pct, holding_hours=8):
    try:
        rates = (float(rate_a), float(rate_b))
        intervals = (float(interval_a), float(interval_b))
        fees = float(fees_pct)
        hold = float(holding_hours)
        if (
            not all(math.isfinite(x) for x in (*rates, *intervals, fees, hold))
            or min(intervals) <= 0
            or fees < 0
            or hold <= 0
            or a == b
        ):
            raise ValueError()
    except (ValueError, TypeError):
        return FundingArb(False, a, b, 0, "INPUT_UNVERIFIED")
    ca = rates[0] * hold / intervals[0]
    cb = rates[1] * hold / intervals[1]
    long, short = (a, b) if ca <= cb else (b, a)
    gross = abs(cb - ca)
    net = gross - fees
    return FundingArb(
        net > 0, long, short, net, "OK" if net > 0 else "NET_CARRY_NONPOSITIVE", gross
    )
