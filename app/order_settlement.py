"""Resolve outstanding quantity before hedging an observed partial fill."""

import asyncio

TERMINAL = {"closed", "filled", "canceled", "cancelled", "rejected", "expired"}


async def settle(executor, result, symbol, requested, timeout=8):
    if result is None:
        return None, "ORDER_UNKNOWN"
    if str(result.status).lower() in TERMINAL or result.filled >= requested - 1e-12:
        return result, "TERMINAL"
    if not result.order_id:
        return None, "ORDER_ID_MISSING"
    try:
        canceled = await asyncio.wait_for(
            executor.cancel(result.order_id, symbol), timeout
        )
        if (
            str(canceled.status).lower() in TERMINAL
            or canceled.filled >= requested - 1e-12
        ):
            return canceled, "TERMINAL"
        current = await asyncio.wait_for(
            executor.order(result.order_id, symbol), timeout
        )
        if (
            str(current.status).lower() in TERMINAL
            or current.filled >= requested - 1e-12
        ):
            return current, "TERMINAL"
    except Exception:
        return None, "CANCEL_OR_RECONCILE_UNKNOWN"
    return None, "ORDER_STILL_WORKING"
