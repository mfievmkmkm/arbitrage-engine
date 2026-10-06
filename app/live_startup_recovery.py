"""Restore DB-backed positions, then require private reconciliation before execution."""

from .live_reconcile_plan import plan


async def recover(durable, runtime, diary, private_trusted):
    rows = await durable.active()
    intents = await diary.order_intent_states()
    unresolved = {
        k: v
        for k, v in intents.items()
        if v not in ("FILLED", "CANCELED", "CANCELLED", "REJECTED")
    }
    try:
        cached = runtime.load()
    except (ValueError, OSError):
        cached = []
    # A crash after the durable close may leave a stale JSON cache entry.
    terminal = set()
    for trade in cached:
        known_row = await durable.get(trade.trade_id)
        if known_row and known_row["phase"] in ("CLOSED_PRIVATE_VERIFIED", "ABORTED"):
            terminal.add(trade.trade_id)
    cached = [trade for trade in cached if trade.trade_id not in terminal]
    actions = plan(rows, cached)
    restored = await durable.runtime_trades()
    known = {x.trade_id for x in restored}
    # Never discard legacy positions with no DB authority.
    restored.extend(
        x
        for x in cached
        if x.trade_id not in known
        and not any(y["trade_id"] == x.trade_id for y in rows)
    )
    if any(x["action"] == "GLOBAL_HALT" for x in actions):
        reason = "RUNTIME_WITHOUT_DB"
    elif unresolved:
        reason = "UNRESOLVED_ORDER_INTENTS"
    elif rows:
        reason = "ACTIVE_DB_REQUIRES_PRIVATE_RECONCILIATION"
    elif not private_trusted:
        reason = "PRIVATE_UNVERIFIED"
    else:
        reason = "EMPTY_RECONCILED"
    runtime.save(restored)
    return {
        "safe": reason == "EMPTY_RECONCILED",
        "reason": reason,
        "active": rows,
        "trades": restored,
        "actions": actions,
        "unknown_intents": unresolved,
    }
