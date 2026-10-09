"""Persist observed order evidence without retrying an exchange write."""

import math
from .order_status import normalize

TERMINAL = {"FILLED", "CANCELED", "CANCELLED", "REJECTED"}


def accounting_missing(meta):
    return float(meta.get("filled") or 0) > 0 and (
        meta.get("fee") is None or meta.get("avg_price") is None
    )


async def reconcile_and_persist(diary, executors, trade_id=None, max_queries=50):
    states = await diary.order_intent_states(trade_id)
    intents = await diary.order_intents(trade_id)
    resolved = dict(states)
    unresolved = []
    queries = 0
    for iid, state in states.items():
        meta = intents.get(iid) or {}
        if state in TERMINAL and not accounting_missing(meta):
            continue
        if queries >= max_queries:
            if state not in TERMINAL:
                unresolved.append(iid)
            continue
        ex = executors.get(meta.get("venue"))
        symbol = meta.get("symbol")
        oid = meta.get("order_id")
        if not ex or not symbol:
            if state not in TERMINAL:
                unresolved.append(iid)
            continue
        queries += 1
        result = None
        if oid:
            try:
                result = await ex.order(oid, symbol)
            except Exception:
                result = None
        if result is None and hasattr(ex, "order_by_client_id"):
            try:
                result = await ex.order_by_client_id(iid, symbol)
            except Exception:
                result = None
        if result is None:
            if state not in TERMINAL:
                unresolved.append(iid)
            continue
        filled = float(result.filled)
        if (
            not math.isfinite(filled)
            or filled < 0
            or filled > float(meta.get("qty") or 0) * (1 + 1e-10)
        ):
            unresolved.append(iid)
            continue
        # Cumulative fills can never shrink after a late acknowledgement/cancel.
        if filled < float(meta.get("filled") or 0) * (1 - 1e-10):
            unresolved.append(iid)
            continue
        n = normalize(result.status, result.filled, meta.get("qty"))
        resolved[iid] = n
        await diary.update_order_intent_reconciled(iid, n, result)
        if n not in TERMINAL:
            unresolved.append(iid)
    return resolved, tuple(unresolved)
