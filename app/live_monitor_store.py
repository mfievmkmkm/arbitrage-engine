"""Persist monitoring, incidents and verified results with idempotent accounting."""

import json, time
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_monitor_state(id INTEGER PRIMARY KEY CHECK(id=1),ts REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS live_incidents(key TEXT PRIMARY KEY,trade_id TEXT,code TEXT,severity TEXT,status TEXT,first_seen REAL,last_seen REAL,payload TEXT);
CREATE TABLE IF NOT EXISTS live_marks(id INTEGER PRIMARY KEY,trade_id TEXT,ts REAL,estimated_net REAL,gross REAL,exit_fee REAL,funding REAL,funding_known INTEGER,exit_signal TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS live_results(trade_id TEXT PRIMARY KEY,ts REAL,gross REAL,fees REAL,funding REAL,net REAL,reason TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS funding_settlements(venue TEXT,event_id TEXT,trade_id TEXT,symbol TEXT,ts REAL,amount REAL,payload TEXT,PRIMARY KEY(venue,event_id));
"""


class Store:
    def __init__(self, path):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA)
            await d.commit()

    async def publish(self, summary, incidents, marks):
        now = time.time()
        new = []
        keys = {x["key"] for x in incidents}
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT key FROM live_incidents WHERE status='ACTIVE'"
            ) as c:
                old = {x[0] for x in await c.fetchall()}
            for x in incidents:
                if x["key"] not in old:
                    new.append(x)
                await d.execute(
                    "INSERT INTO live_incidents VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET status='ACTIVE',last_seen=excluded.last_seen,payload=excluded.payload",
                    (
                        x["key"],
                        x.get("trade_id", ""),
                        x["code"],
                        x["severity"],
                        "ACTIVE",
                        now,
                        now,
                        json.dumps(x),
                    ),
                )
            for key in old - keys:
                await d.execute(
                    "UPDATE live_incidents SET status='RESOLVED',last_seen=? WHERE key=?",
                    (now, key),
                )
            for x in marks:
                await d.execute(
                    "INSERT INTO live_marks(trade_id,ts,estimated_net,gross,exit_fee,funding,funding_known,exit_signal,payload) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        x["trade_id"],
                        now,
                        x.get("estimated_net"),
                        x.get("gross"),
                        x.get("exit_fee"),
                        x.get("funding", 0),
                        int(x.get("funding_known", False)),
                        x.get("exit_signal", "HOLD"),
                        json.dumps(x),
                    ),
                )
            await d.execute(
                "INSERT INTO live_monitor_state VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET ts=excluded.ts,payload=excluded.payload",
                (now, json.dumps(summary)),
            )
            await d.commit()
        return new

    async def latest(self):
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT payload FROM live_monitor_state WHERE id=1"
            ) as c:
                row = await c.fetchone()
            return json.loads(row[0]) if row else None

    async def settlements(self, trade_id, events):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            for x in events:
                async with d.execute(
                    "SELECT trade_id,amount FROM funding_settlements WHERE venue=? AND event_id=?",
                    (x["venue"], x["event_id"]),
                ) as c:
                    old = await c.fetchone()
                if old and (old[0] != trade_id or abs(old[1] - x["amount"]) > 1e-10):
                    raise ValueError("FUNDING_ATTRIBUTION_CONFLICT")
                await d.execute(
                    "INSERT OR IGNORE INTO funding_settlements VALUES(?,?,?,?,?,?,?)",
                    (
                        x["venue"],
                        x["event_id"],
                        trade_id,
                        x["symbol"],
                        x["ts"],
                        x["amount"],
                        json.dumps(x),
                    ),
                )
            await d.commit()

    async def finalize(self, trade_id, result, proof):
        if (
            not proof.get("private_flat")
            or not proof.get("orders_terminal")
            or not proof.get("funding_verified")
        ):
            raise ValueError("CLOSE_PROOF_INCOMPLETE")
        now = time.time()
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            async with d.execute(
                "SELECT phase,payload FROM live_trades WHERE trade_id=?", (trade_id,)
            ) as c:
                row = await c.fetchone()
            if row is None:
                raise ValueError("DURABLE_TRADE_MISSING")
            async with d.execute(
                "SELECT trade_id FROM live_results WHERE trade_id=?", (trade_id,)
            ) as c:
                if await c.fetchone():
                    return False
            payload = json.loads(row[1])
            payload.update(result=result, close_proof=proof)
            await d.execute(
                "INSERT INTO live_results VALUES(?,?,?,?,?,?,?,?)",
                (
                    trade_id,
                    now,
                    result["gross"],
                    result["fees"],
                    result["funding"],
                    result["net"],
                    result["reason"],
                    json.dumps(result),
                ),
            )
            await d.execute(
                "UPDATE live_trades SET phase='CLOSED_PRIVATE_VERIFIED',updated_at=?,payload=? WHERE trade_id=?",
                (now, json.dumps(payload), trade_id),
            )
            for kind in ("TRADE_RESULT", "STATE"):
                event = {
                    "trade_id": trade_id,
                    "ts": now,
                    "kind": kind,
                    "phase": "CLOSED_PRIVATE_VERIFIED",
                    "net": result["net"],
                    "reason": result["reason"],
                    "proof": proof,
                }
                await d.execute(
                    "INSERT INTO execution_events(trade_id,ts,kind,reason,payload) VALUES(?,?,?,?,?)",
                    (trade_id, now, kind, result["reason"], json.dumps(event)),
                )
            await d.commit()
        return True

    async def totals(self):
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT COUNT(*),COALESCE(SUM(net),0),COALESCE(SUM(fees),0),COALESCE(SUM(funding),0) FROM live_results"
            ) as c:
                row = await c.fetchone()
            return {"closed": row[0], "net": row[1], "fees": row[2], "funding": row[3]}
