import asyncio
from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .recovery_executor import plan
from .order_settlement import settle
import math


@dataclass(frozen=True)
class RecoveryResult:
    action: str
    completed: bool
    result: object = None
    error: str = ""


async def recover(
    symbol,
    long_venue,
    short_venue,
    long_base,
    short_base,
    long_contract_size,
    short_contract_size,
    long_executor,
    short_executor,
    remaining_edge,
    complete_cost,
    flatten_cost,
    round_qty=None,
    timeout=8,
    trade_id=None,
    short_round=None,
):
    try:
        values = (long_base, short_base, long_contract_size, short_contract_size)
        if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in values):
            raise ValueError()
        if (
            min(long_base, short_base) < 0
            or min(long_contract_size, short_contract_size) <= 0
        ):
            raise ValueError()
    except (TypeError, ValueError):
        return RecoveryResult("BLOCKED", False, None, "INVALID_RECOVERY_EXPOSURE")
    p = plan(
        long_venue,
        short_venue,
        long_base,
        short_base,
        remaining_edge,
        complete_cost,
        flatten_cost,
    )
    if p.action == "HEDGED":
        return RecoveryResult("HEDGED", True)
    if p.venue == long_venue:
        executor = long_executor
        cs = long_contract_size
    else:
        executor = short_executor
        cs = short_contract_size
    qty = p.base_amount / cs
    requested = qty
    rounder = short_round if p.venue == short_venue and short_round else round_qty
    if rounder:
        try:
            qty = rounder(qty)
        except Exception:
            return RecoveryResult(p.action, False, None, "RECOVERY_ROUNDING_FAILED")
    if (
        isinstance(qty, bool)
        or not isinstance(qty, (int, float))
        or not math.isfinite(qty)
        or qty > requested + max(1e-12, requested * 1e-10)
    ):
        return RecoveryResult(
            p.action, False, None, "RECOVERY_QTY_INCREASED_OR_INVALID"
        )
    if qty <= 0:
        return RecoveryResult(p.action, False, None, "ZERO_RECOVERY_QTY")
    req = SubmitRequest(
        symbol, p.side, qty, "market", None, p.action == "FLATTEN", False
    )
    try:
        if trade_id:
            from .live_order_intent import OrderIntent

            iid = f"{trade_id}:entry-recovery:{p.venue}:{p.side}"
            req = SubmitRequest(
                symbol, p.side, qty, "market", None, p.action == "FLATTEN", False, iid
            )
            intent = OrderIntent(
                iid, trade_id, p.venue, symbol, p.side, qty, p.action == "FLATTEN"
            )
            r, status = await asyncio.wait_for(
                executor.submit_intent(intent, req), timeout
            )
            if r is None:
                return RecoveryResult(p.action, False, None, status)
        else:
            r = await asyncio.wait_for(executor.submit(req), timeout)
    except Exception as e:
        return RecoveryResult(p.action, False, None, type(e).__name__)
    r, evidence = await settle(executor, r, symbol, qty, timeout)
    if r is None:
        return RecoveryResult(p.action, False, None, evidence)
    exact = abs(r.filled * cs - p.base_amount) <= max(1e-12, p.base_amount * 1e-10)
    return RecoveryResult(p.action, exact, r, "" if exact else "RECOVERY_RESIDUAL")
