from dataclasses import dataclass
from .live_order_intent import OrderIntent
from .exchange_executor import SubmitRequest
from .exit_runner import ExitResult
from .fill_reconcile import reconcile
from .order_settlement import settle
import asyncio


@dataclass(frozen=True)
class PersistedExit:
    execution: object
    status: str


async def run(trade, long_executor, short_executor, timeout=8):
    async def leg(name, venue, side, qty, ex):
        iid = trade.trade_id + ":" + name
        intent = OrderIntent(iid, trade.trade_id, venue, trade.symbol, side, qty, True)
        req = SubmitRequest(trade.symbol, side, qty, "market", None, True, False, iid)
        try:
            return await asyncio.wait_for(ex.submit_intent(intent, req), timeout)
        except Exception as e:
            return None, "SUBMIT_" + type(e).__name__

    l, s = await asyncio.gather(
        leg("exit-long", trade.long_venue, "sell", trade.long_contracts, long_executor),
        leg(
            "exit-short",
            trade.short_venue,
            "buy",
            trade.short_contracts,
            short_executor,
        ),
    )
    lr, ls = l
    sr, ss = s
    if lr is None or sr is None:
        return PersistedExit(None, "EXIT_UNCERTAIN:" + ls + ":" + ss)
    (lr, lc), (sr, sc) = await asyncio.gather(
        settle(long_executor, lr, trade.symbol, trade.long_contracts, timeout),
        settle(short_executor, sr, trade.symbol, trade.short_contracts, timeout),
    )
    if lr is None or sr is None:
        return PersistedExit(None, "EXIT_WORKING_ORDER_UNCERTAIN:" + lc + ":" + sc)
    r = reconcile(
        lr.filled, trade.long_contract_size, sr.filled, trade.short_contract_size
    )
    flat = (
        lr.filled >= trade.long_contracts - 1e-12
        and sr.filled >= trade.short_contracts - 1e-12
    )
    return PersistedExit(
        ExitResult(lr, sr, flat, r.mismatch_pct), "FILLED" if flat else "PARTIAL"
    )
