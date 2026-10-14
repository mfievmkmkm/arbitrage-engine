import asyncio
from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .order_settlement import settle
from .recovery_intent_factory import close as recovery_intent
import math


@dataclass(frozen=True)
class PrivateRecovery:
    recovered: bool
    reason: str
    long_result: object = None
    short_result: object = None


def _contracts(snapshot, venue, symbol, side, tolerance=1e-12):
    row = snapshot.get(venue)
    if not row or not getattr(row.get("health"), "ok", False):
        return None
    total = 0.0
    opposite = 0.0
    if not isinstance(row.get("positions"), list):
        return None
    for p in row["positions"]:
        if p.symbol != symbol:
            continue
        if getattr(p, "venue", venue) != venue:
            return None
        q = p.contracts
        try:
            if q is None:
                size = float(p.contract_size)
                if not math.isfinite(size) or size <= 0:
                    return None
                q = abs(float(p.qty)) / size
            if isinstance(q, bool) or not math.isfinite(float(q)):
                return None
            q = abs(float(q))
        except (TypeError, ValueError, AttributeError):
            return None
        if str(p.side).lower() == side:
            total += q
        elif q > tolerance:
            opposite += q
    return total, opposite


async def recover_from_private(
    trade,
    snapshot,
    long_executor,
    short_executor,
    timeout=8,
    tolerance=1e-12,
    market_reader=None,
):
    a = _contracts(snapshot, trade.long_venue, trade.symbol, "long", tolerance)
    b = _contracts(snapshot, trade.short_venue, trade.symbol, "short", tolerance)
    if a is None or b is None:
        return PrivateRecovery(False, "PRIVATE_STATE_UNTRUSTED")
    if a[1] > tolerance or b[1] > tolerance:
        return PrivateRecovery(False, "OPPOSITE_OR_FLIPPED_EXPOSURE")
    l, s = a[0], b[0]
    if l > trade.long_contracts + tolerance or s > trade.short_contracts + tolerance:
        return PrivateRecovery(False, "PRIVATE_EXPOSURE_EXCEEDS_TRADE")
    if l <= tolerance and s <= tolerance:
        return PrivateRecovery(True, "ALREADY_FLAT")
    requests = []
    if l > tolerance:
        requests.append(
            (
                "long",
                long_executor,
                SubmitRequest(trade.symbol, "sell", l, "market", None, True, False),
            )
        )
    if s > tolerance:
        requests.append(
            (
                "short",
                short_executor,
                SubmitRequest(trade.symbol, "buy", s, "market", None, True, False),
            )
        )

    from .recovery_market import prepare as quote, actual_slippage

    try:
        prepared = await asyncio.gather(
            *(
                quote(
                    market_reader,
                    trade.long_venue if leg == "long" else trade.short_venue,
                    r,
                    (
                        trade.long_contract_size
                        if leg == "long"
                        else trade.short_contract_size
                    ),
                    timeout,
                )
                for leg, ex, r in requests
            )
        )
        requests = [(leg, ex, r) for (leg, ex, _), r in zip(requests, prepared)]
    except Exception as e:
        return PrivateRecovery(False, "PRIVATE_RECOVERY_QUOTE_BLOCKED:" + str(e))

    async def submit(leg, ex, r):
        try:
            if hasattr(ex, "submit_intent"):
                venue = trade.long_venue if leg == "long" else trade.short_venue
                intent = recovery_intent(
                    trade.trade_id, venue, trade.symbol, r.side, r.qty
                )
                from dataclasses import replace

                r = replace(r, client_order_id=intent.intent_id)
                result, state = await asyncio.wait_for(
                    ex.submit_intent(intent, r), timeout
                )
                if result is None:
                    return RuntimeError(state)
            else:
                result = await asyncio.wait_for(ex.submit(r), timeout)
            result, state = await settle(ex, result, r.symbol, r.qty, timeout)
            return result if result is not None else RuntimeError(state)
        except Exception as e:
            return e

    results = await asyncio.gather(*(submit(leg, ex, r) for leg, ex, r in requests))
    out = {"long": None, "short": None}
    for (leg, _, req), r in zip(requests, results):
        if not isinstance(r, Exception):
            out[leg] = r
    for (leg, _, req), r in zip(requests, results):
        if isinstance(r, Exception):
            return PrivateRecovery(
                False, "PRIVATE_RECOVERY_" + type(r).__name__, out["long"], out["short"]
            )
        out[leg] = r
        if actual_slippage(req, r):
            return PrivateRecovery(
                False, "RECOVERY_ACTUAL_SLIPPAGE_STOP", out["long"], out["short"]
            )
        if r.filled > req.qty + tolerance:
            return PrivateRecovery(
                False, "PRIVATE_RECOVERY_OVERFILL", out["long"], out["short"]
            )
        if r.filled < req.qty - tolerance:
            return PrivateRecovery(
                False, "PRIVATE_RECOVERY_PARTIAL", out["long"], out["short"]
            )
    return PrivateRecovery(
        True, "PRIVATE_RECOVERY_SUBMITTED", out["long"], out["short"]
    )
