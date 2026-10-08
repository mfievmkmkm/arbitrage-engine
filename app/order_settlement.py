"""Resolve outstanding quantity without losing cumulative fill evidence."""

import asyncio, math

TERMINAL = {"closed", "filled", "canceled", "cancelled", "rejected", "expired"}


def valid(result, requested, minimum=0, order_id=None):
    try:
        q = float(result.filled)
        target = float(requested)
        tolerance = max(target * 1e-10, 1e-12)
        if (
            not all(math.isfinite(x) for x in (q, target))
            or target <= 0
            or not minimum - tolerance <= q <= target + tolerance
        ):
            return False
        if order_id and result.order_id != order_id:
            return False
        if q > 0 and (
            result.avg_price is None
            or not math.isfinite(float(result.avg_price))
            or result.avg_price <= 0
            or result.fee is None
            or not math.isfinite(float(result.fee))
        ):
            return False
        return True
    except (AttributeError, ValueError, TypeError):
        return False


def terminal(result, requested):
    return str(result.status).lower() in TERMINAL or result.filled >= requested - max(
        requested * 1e-10, 1e-12
    )


async def settle(executor, result, symbol, requested, timeout=8):
    if result is None:
        return None, "ORDER_UNKNOWN"
    if not valid(result, requested):
        return None, "ORDER_EVIDENCE_INVALID"
    if terminal(result, requested):
        return result, "TERMINAL"
    if not result.order_id:
        return None, "ORDER_ID_MISSING"
    try:
        canceled = await asyncio.wait_for(
            executor.cancel(result.order_id, symbol), timeout
        )
        if not valid(canceled, requested, result.filled, result.order_id):
            return None, "CANCEL_EVIDENCE_CONFLICT"
        if terminal(canceled, requested):
            return canceled, "TERMINAL"
        current = await asyncio.wait_for(
            executor.order(result.order_id, symbol), timeout
        )
        if not valid(current, requested, canceled.filled, result.order_id):
            return None, "LOOKUP_EVIDENCE_CONFLICT"
        if terminal(current, requested):
            return current, "TERMINAL"
    except Exception:
        return None, "CANCEL_OR_RECONCILE_UNKNOWN"
    return None, "ORDER_STILL_WORKING"
