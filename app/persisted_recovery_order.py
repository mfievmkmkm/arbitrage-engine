import asyncio
from .live_order_intent import OrderIntent
from .exchange_executor import SubmitRequest


async def submit(
    executor,
    diary,
    trade_id,
    venue,
    symbol,
    side,
    qty,
    reduce_only,
    timeout,
    tag,
    market_reader=None,
    contract_size=None,
):
    intent = OrderIntent(
        f"{trade_id}:{tag}", trade_id, venue, symbol, side, qty, reduce_only
    )
    req = SubmitRequest(
        symbol, side, qty, "market", None, reduce_only, False, intent.intent_id
    )
    try:
        from .recovery_market import prepare as quote

        req = await quote(market_reader, venue, req, contract_size, timeout)
        r, state = await asyncio.wait_for(executor.submit_intent(intent, req), timeout)
    except Exception:
        return None, "UNKNOWN"
    return r, state
