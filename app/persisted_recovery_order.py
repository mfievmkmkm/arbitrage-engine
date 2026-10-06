import asyncio
from .live_order_intent import OrderIntent
from .exchange_executor import SubmitRequest


async def submit(
    executor, diary, trade_id, venue, symbol, side, qty, reduce_only, timeout, tag
):
    intent = OrderIntent(
        f"{trade_id}:{tag}", trade_id, venue, symbol, side, qty, reduce_only
    )
    req = SubmitRequest(
        symbol, side, qty, "market", None, reduce_only, False, intent.intent_id
    )
    try:
        r, state = await asyncio.wait_for(executor.submit_intent(intent, req), timeout)
    except Exception:
        return None, "UNKNOWN"
    return r, state
