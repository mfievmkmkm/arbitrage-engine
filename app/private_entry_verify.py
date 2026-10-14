import asyncio
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EntryPrivateCheck:
    verified: bool
    reason: str
    long_base: float = 0
    short_base: float = 0


def _side(snapshot, venue, symbol, expected):
    row = snapshot.get(venue)
    if not row or not getattr(row.get("health"), "ok", False):
        return None, "PRIVATE_STATE_UNTRUSTED"
    if not isinstance(row.get("positions"), list):
        return None, "PRIVATE_STATE_UNTRUSTED"
    good = bad = 0.0
    for p in row.get("positions", []):
        if p.symbol != symbol:
            continue
        try:
            if isinstance(p.qty, bool):
                return None, "PRIVATE_QUANTITY_INVALID"
            q = abs(float(p.qty))
            if not math.isfinite(q):
                return None, "PRIVATE_QUANTITY_INVALID"
        except (ValueError, TypeError, AttributeError):
            return None, "PRIVATE_QUANTITY_INVALID"
        if str(p.side).lower() == expected:
            good += q
        elif q > 0:
            bad += q
    if bad > 0:
        return None, "OPPOSITE_OR_FLIPPED_EXPOSURE"
    return good, "OK"


async def verify(
    snapshot_source,
    symbol,
    long_venue,
    short_venue,
    expected_base,
    attempts=3,
    delay=0.25,
    tolerance_pct=0.001,
):
    last = "PRIVATE_STATE_UNTRUSTED"
    for i in range(max(1, attempts)):
        try:
            snap = snapshot_source()
            if hasattr(snap, "__await__"):
                snap = await snap
        except Exception:
            snap = None
        if snap:
            l, lr = _side(snap, long_venue, symbol, "long")
            s, sr = _side(snap, short_venue, symbol, "short")
            if l is not None and s is not None:
                scale = max(expected_base, l, s)
                if (
                    abs(l - expected_base) <= scale * tolerance_pct
                    and abs(s - expected_base) <= scale * tolerance_pct
                    and abs(l - s) <= scale * tolerance_pct
                ):
                    return EntryPrivateCheck(True, "VERIFIED", l, s)
                last = "PRIVATE_POSITION_MISMATCH"
            else:
                last = lr if l is None else sr
        if i + 1 < attempts:
            await asyncio.sleep(delay)
    return EntryPrivateCheck(False, last)
