from dataclasses import dataclass
from .live_order_intent import OrderIntent
from .exchange_executor import SubmitRequest
from .exit_runner import ExitResult
from .fill_reconcile import reconcile
from .order_settlement import settle
import asyncio
import math


@dataclass(frozen=True)
class PersistedExit:
    execution: object
    status: str


async def run(trade, long_executor, short_executor, timeout=8, market_reader=None):
    from .recovery_market import prepare as quote, actual_slippage

    try:
        reqs = await asyncio.gather(
            quote(
                market_reader,
                trade.long_venue,
                SubmitRequest(
                    trade.symbol, "sell", trade.long_contracts, "market", None, True
                ),
                trade.long_contract_size,
                timeout,
            ),
            quote(
                market_reader,
                trade.short_venue,
                SubmitRequest(
                    trade.symbol, "buy", trade.short_contracts, "market", None, True
                ),
                trade.short_contract_size,
                timeout,
            ),
        )
    except Exception as e:
        return PersistedExit(None, "EXIT_QUOTE_BLOCKED:" + str(e))

    async def leg(name, venue, side, qty, ex):
        iid = trade.trade_id + ":" + name
        intent = OrderIntent(iid, trade.trade_id, venue, trade.symbol, side, qty, True)
        from dataclasses import replace

        req = replace(reqs[0 if side == "sell" else 1], client_order_id=iid)
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
        math.isclose(lr.filled, trade.long_contracts, rel_tol=1e-10, abs_tol=0)
        and math.isclose(sr.filled, trade.short_contracts, rel_tol=1e-10, abs_tol=0)
    )
    slip = actual_slippage(reqs[0], lr) or actual_slippage(reqs[1], sr)
    return PersistedExit(
        ExitResult(lr, sr, flat, r.mismatch_pct),
        "EXIT_ACTUAL_SLIPPAGE_STOP" if slip else ("FILLED" if flat else "PARTIAL"),
    )
