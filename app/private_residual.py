from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Residual:
    flat: bool
    long_qty: float
    short_qty: float
    reason: str


def verify(snapshot, symbol, long_venue, short_venue, tolerance=0):
    def exposure(venue):
        row = snapshot.get(venue)
        if not row or not getattr(row.get("health"), "ok", False):
            return None
        if not isinstance(row.get("positions"), list):
            return None
        long_qty = short_qty = 0.0
        for p in row.get("positions", []):
            if p.symbol != symbol:
                continue
            try:
                if isinstance(p.qty, bool):
                    return None
                q = abs(float(p.qty))
                if not math.isfinite(q):
                    return None
            except (ValueError, TypeError, AttributeError):
                return None
            side = str(p.side).lower()
            if side == "long":
                long_qty += q
            elif side == "short":
                short_qty += q
            elif q > tolerance:
                return (q, q, True)
        return (long_qty, short_qty, False)

    a = exposure(long_venue)
    b = exposure(short_venue)
    if a is None or b is None:
        return Residual(False, 0, 0, "PRIVATE_STATE_UNTRUSTED")
    if a[2] or b[2]:
        return Residual(False, a[0] + b[0], a[1] + b[1], "UNKNOWN_POSITION_SIDE")
    long_total = a[0] + b[0]
    short_total = a[1] + b[1]
    if long_total > tolerance or short_total > tolerance:
        expected_only = a[0] <= tolerance and b[1] <= tolerance
        reason = (
            "OPPOSITE_OR_FLIPPED_EXPOSURE"
            if expected_only and (a[1] > tolerance or b[0] > tolerance)
            else "RESIDUAL_EXPOSURE"
        )
        return Residual(False, long_total, short_total, reason)
    return Residual(True, 0, 0, "FLAT")
