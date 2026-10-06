import asyncio
from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .recovery_executor import plan


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
    rounder = short_round if p.venue == short_venue and short_round else round_qty
    if rounder:
        qty = rounder(qty)
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
    return RecoveryResult(p.action, r.filled >= qty - 1e-12, r)
