"""Durable live authority. Runtime JSON is a cache, never execution evidence."""

import json
import time
import aiosqlite
from .runtime_state import RuntimeTrade

SCHEMA = """CREATE TABLE IF NOT EXISTS live_trades(
 trade_id TEXT PRIMARY KEY,phase TEXT NOT NULL,symbol TEXT,long_venue TEXT,
 short_venue TEXT,planned_long REAL,planned_short REAL,actual_long REAL DEFAULT 0,
 actual_short REAL DEFAULT 0,long_price REAL,short_price REAL,fees REAL DEFAULT 0,
 updated_at REAL NOT NULL,payload TEXT NOT NULL DEFAULT '{}')"""
TERMINAL = {"CLOSED_PRIVATE_VERIFIED", "ABORTED"}


class Store:
    def __init__(self, path):
        self.path = str(path)

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.execute(SCHEMA)
            await d.commit()

    async def phase(self, trade_id, phase, **meta):
        if not trade_id:
            raise ValueError("TRADE_ID_REQUIRED")
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?", (trade_id,)
            ) as c:
                old = await c.fetchone()
            if old and old[0] in TERMINAL and phase != old[0]:
                raise ValueError("TERMINAL_TRADE_CANNOT_REOPEN")
            payload = json.loads(old[1]) if old else {}
            payload.update(meta)
            await d.execute(
                """INSERT INTO live_trades(trade_id,phase,symbol,long_venue,short_venue,
    planned_long,planned_short,actual_long,actual_short,long_price,short_price,fees,updated_at,payload)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_id) DO UPDATE SET
    phase=excluded.phase,symbol=excluded.symbol,long_venue=excluded.long_venue,
    short_venue=excluded.short_venue,planned_long=excluded.planned_long,
    planned_short=excluded.planned_short,actual_long=excluded.actual_long,
    actual_short=excluded.actual_short,long_price=excluded.long_price,
    short_price=excluded.short_price,fees=excluded.fees,updated_at=excluded.updated_at,payload=excluded.payload""",
                (
                    trade_id,
                    phase,
                    payload.get("symbol"),
                    payload.get("long_venue"),
                    payload.get("short_venue"),
                    payload.get("planned_long"),
                    payload.get("planned_short"),
                    payload.get("actual_long", 0),
                    payload.get("actual_short", 0),
                    payload.get("long_price"),
                    payload.get("short_price"),
                    payload.get("fees", 0),
                    time.time(),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
            await d.commit()

    async def get(self, trade_id):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT * FROM live_trades WHERE trade_id=?", (trade_id,)
            ) as c:
                row = await c.fetchone()
                return dict(row) if row else None

    async def reserve_entry(self, trade_id, **meta):
        """Atomic single-position micro-live capacity, across all processes."""
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT 1 FROM live_trades WHERE trade_id=? OR phase NOT IN ('CLOSED_PRIVATE_VERIFIED','ABORTED') LIMIT 1",
                (trade_id,),
            ) as c:
                if await c.fetchone():
                    return False
            await d.execute(
                "INSERT INTO live_trades(trade_id,phase,symbol,long_venue,short_venue,planned_long,planned_short,updated_at,payload) VALUES(?,'PLANNED',?,?,?,?,?,?,?)",
                (
                    trade_id,
                    meta["symbol"],
                    meta["long_venue"],
                    meta["short_venue"],
                    meta["planned_long"],
                    meta["planned_short"],
                    time.time(),
                    json.dumps(meta),
                ),
            )
            await d.commit()
            return True

    async def claim_exit(self, trade, signal):
        """Reserve one exit before any send; compare the authoritative position.

        Concurrent processes cannot both reserve. A crash after reservation
        requires reconciliation, never an automatic repeat of the exit.
        """
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT phase,payload,symbol,long_venue,short_venue FROM live_trades WHERE trade_id=?",
                (trade.trade_id,),
            ) as c:
                row = await c.fetchone()
            if row is None or row[0] not in ("OPEN", "HEDGED_PRIVATE_VERIFIED"):
                return False
            payload = json.loads(row[1])
            if (
                payload.get("runtime_trade") != trade.row()
                or tuple(row[2:]) != (trade.symbol, trade.long_venue, trade.short_venue)
                or payload.get("entry_hold_reason")
                or payload.get("exit_hold_reason")
            ):
                return False
            payload.update(
                exit_dispatch_signal=signal, exit_dispatch_started_at=time.time()
            )
            await d.execute(
                "UPDATE live_trades SET phase='EXIT_SUBMITTING',updated_at=?,payload=? WHERE trade_id=?",
                (time.time(), json.dumps(payload), trade.trade_id),
            )
            await d.commit()
            return True

    async def active(self):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT * FROM live_trades WHERE phase NOT IN ('CLOSED_PRIVATE_VERIFIED','ABORTED') ORDER BY updated_at"
            ) as c:
                return [dict(x) for x in await c.fetchall()]

    async def mark_open(self, trade_id, **meta):
        """A stale observer cannot overwrite a concurrently claimed exit."""
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?", (trade_id,)
            ) as c:
                row = await c.fetchone()
            if row is None or row[0] not in ("OPEN", "HEDGED_PRIVATE_VERIFIED"):
                return False
            payload = json.loads(row[1])
            payload.update(meta)
            await d.execute(
                "UPDATE live_trades SET phase='OPEN',updated_at=?,payload=? WHERE trade_id=?",
                (time.time(), json.dumps(payload), trade_id),
            )
            await d.commit()
            return True

    async def runtime_trades(self):
        trades = []
        for row in await self.active():
            payload = json.loads(row["payload"])
            if row["phase"] in (
                "HEDGED_PRIVATE_VERIFIED",
                "OPEN",
                "EXIT_SUBMITTING",
            ) and payload.get("runtime_trade"):
                trades.append(RuntimeTrade(**payload["runtime_trade"]))
        return trades
