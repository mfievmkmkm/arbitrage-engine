"""Explicitly abandon a never-claimed cash entry, with an atomic journal check."""

import json
import aiosqlite


async def abort(store, row, now):
    async with aiosqlite.connect(store.path) as d:
        await d.execute("BEGIN IMMEDIATE")
        cur = await d.execute(
            "SELECT phase,payload FROM live_trades WHERE trade_id=?", (row["trade_id"],)
        )
        if await cur.fetchone() != ("PLANNED", row["payload"]):
            return dict(status="RECONCILE_REQUIRED")
        cur = await d.execute(
            "SELECT 1 FROM order_intents WHERE trade_id=? LIMIT 1", (row["trade_id"],)
        )
        if await cur.fetchone():
            return dict(status="RECONCILE_REQUIRED")
        meta = json.loads(row["payload"])
        meta["cash_reason"] = "EXPLICIT_ABORT_NO_CLAIM_OR_INTENT_PRIVATE_VERIFIED"
        await d.execute(
            "UPDATE live_trades SET phase='ABORTED',payload=?,updated_at=? WHERE trade_id=?",
            (json.dumps(meta), now, row["trade_id"]),
        )
        await d.commit()
    return dict(status="ABORTED", trade_id=row["trade_id"])
