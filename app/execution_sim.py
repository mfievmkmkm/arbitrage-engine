import math
from dataclasses import dataclass


@dataclass
class SimFill:
    requested: float
    filled: float
    price: float | None


def walk(levels, qty):
    if not math.isfinite(qty) or qty <= 0:
        raise ValueError("SIM_QUANTITY_INVALID")
    if any(
        not all(math.isfinite(x) for x in (p, a)) or p <= 0 or a < 0 for p, a in levels
    ):
        raise ValueError("SIM_BOOK_INVALID")
    remaining = qty
    cost = 0.0
    filled = 0.0
    for price, amount in levels:
        take = min(remaining, amount)
        cost += take * price
        filled += take
        remaining -= take
        if remaining <= qty * 1e-12:
            break
    if not math.isfinite(cost):
        raise ValueError("SIM_NOTIONAL_INVALID")
    return SimFill(qty, filled, cost / filled if filled else None)


def hedge_sim(long_asks, short_bids, qty):
    a = walk(long_asks, qty)
    b = walk(short_bids, qty)
    return {
        "long": a,
        "short": b,
        "matched_qty": min(a.filled, b.filled),
        "unhedged_qty": abs(a.filled - b.filled),
        "complete": a.filled >= qty - 1e-12 and b.filled >= qty - 1e-12,
    }
