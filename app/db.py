import json, aiosqlite, time

SCHEMA = """CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,ts REAL,symbol TEXT,buy TEXT,sell TEXT,raw REAL,executable REAL,fee_pct REAL,hypothetical_edge REAL,notional REAL,payload TEXT);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts);
CREATE TABLE IF NOT EXISTS paper_positions(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,buy TEXT,sell TEXT,notional REAL,entry_buy REAL,entry_sell REAL,entry_spread REAL,opened_at REAL,best_net_usd REAL,current_net_usd REAL,current_spread REAL,status TEXT,closed_at REAL,close_reason TEXT);
CREATE TABLE IF NOT EXISTS paper_marks(id INTEGER PRIMARY KEY AUTOINCREMENT,position_id INTEGER,ts REAL,net_usd REAL,spread REAL);\nCREATE TABLE IF NOT EXISTS execution_events(id INTEGER PRIMARY KEY AUTOINCREMENT,trade_id TEXT,ts REAL,kind TEXT,venue TEXT,symbol TEXT,side TEXT,qty REAL,price REAL,fee REAL,reason TEXT,payload TEXT);\nCREATE INDEX IF NOT EXISTS idx_execution_trade ON execution_events(trade_id,ts);\nCREATE TABLE IF NOT EXISTS order_intents(intent_id TEXT PRIMARY KEY,trade_id TEXT,venue TEXT,symbol TEXT,side TEXT,qty REAL,reduce_only INTEGER,state TEXT,updated_at REAL,payload TEXT);"""


class Diary:
    def __init__(self, path):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(SCHEMA)
            await d.execute(
                "CREATE TABLE IF NOT EXISTS order_request_evidence(intent_id TEXT PRIMARY KEY,trade_id TEXT,ts REAL,payload TEXT)"
            )
            await d.execute(
                "CREATE TABLE IF NOT EXISTS signal_decisions(id INTEGER PRIMARY KEY,ts REAL,strategy TEXT,symbol TEXT,buy TEXT,sell TEXT,action TEXT,reason TEXT,payload TEXT)"
            )
            async with d.execute("PRAGMA table_info(paper_positions)") as c:
                columns = {x[1] for x in await c.fetchall()}
            for name in ("base_qty", "entry_fees_usd", "safety_usd"):
                if name not in columns:
                    await d.execute(
                        "ALTER TABLE paper_positions ADD COLUMN " + name + " REAL"
                    )
            async with d.execute("PRAGMA table_info(paper_marks)") as c:
                mark_columns = {x[1] for x in await c.fetchall()}
            if "payload" not in mark_columns:
                await d.execute("ALTER TABLE paper_marks ADD COLUMN payload TEXT")
            await d.commit()

    async def record(self, ops):
        if not ops:
            return
        async with aiosqlite.connect(self.path) as d:
            await d.executemany(
                "INSERT INTO observations(ts,symbol,buy,sell,raw,executable,fee_pct,hypothetical_edge,notional,payload) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        o["ts"],
                        o["symbol"],
                        o["buy"],
                        o["sell"],
                        o["raw"],
                        o["executable"],
                        o["fee_pct"],
                        o["hypothetical_edge"],
                        o["notional"],
                        json.dumps(o),
                    )
                    for o in ops
                ],
            )
            await d.commit()

    async def summary(self):
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT COUNT(*),MAX(ts),MAX(hypothetical_edge) FROM observations"
            ) as c:
                return await c.fetchone()

    async def create_paper_position(self, p):
        async with aiosqlite.connect(self.path) as d:
            c = await d.execute(
                "INSERT INTO paper_positions(symbol,buy,sell,notional,entry_buy,entry_sell,entry_spread,opened_at,best_net_usd,current_net_usd,current_spread,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    p["symbol"],
                    p["buy"],
                    p["sell"],
                    p["notional"],
                    p["entry_buy"],
                    p["entry_sell"],
                    p["entry_spread"],
                    p["opened_at"],
                    p["best_net_usd"],
                    p["current_net_usd"],
                    p["current_spread"],
                    p["status"],
                ),
            )
            await d.execute(
                "UPDATE paper_positions SET base_qty=?,entry_fees_usd=?,safety_usd=? WHERE id=?",
                (
                    p.get("base_qty"),
                    p.get("entry_fees_usd"),
                    p.get("safety_usd", 0),
                    c.lastrowid,
                ),
            )
            if p.get("last_mark"):
                await d.execute(
                    "INSERT INTO paper_marks(position_id,ts,net_usd,spread,payload) VALUES(?,?,?,?,?)",
                    (
                        c.lastrowid,
                        p["last_mark"]["ts"],
                        p["current_net_usd"],
                        p["current_spread"],
                        json.dumps(p["last_mark"]),
                    ),
                )
            await d.commit()
            return c.lastrowid

    async def update_paper_position(self, p):
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "UPDATE paper_positions SET best_net_usd=?,current_net_usd=?,current_spread=?,base_qty=?,entry_fees_usd=?,safety_usd=? WHERE id=?",
                (
                    p["best_net_usd"],
                    p["current_net_usd"],
                    p["current_spread"],
                    p.get("base_qty"),
                    p.get("entry_fees_usd"),
                    p.get("safety_usd", 0),
                    p["id"],
                ),
            )
            await d.execute(
                "INSERT INTO paper_marks(position_id,ts,net_usd,spread,payload) VALUES(?,?,?,?,?)",
                (
                    p["id"],
                    (p.get("last_mark") or {}).get("ts", time.time()),
                    p["current_net_usd"],
                    p["current_spread"],
                    json.dumps(p.get("last_mark")),
                ),
            )
            await d.commit()

    async def close_paper_position(self, p, reason):
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "UPDATE paper_positions SET status='CLOSED',closed_at=?,close_reason=?,best_net_usd=?,current_net_usd=?,current_spread=? WHERE id=?",
                (
                    (p.get("last_mark") or {}).get("ts", time.time()),
                    reason,
                    p["best_net_usd"],
                    p["current_net_usd"],
                    p["current_spread"],
                    p["id"],
                ),
            )
            await d.commit()

    async def open_paper_positions(self):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT id,symbol,buy,sell,notional,entry_buy,entry_sell,entry_spread,opened_at,best_net_usd,current_net_usd,current_spread,status,base_qty,entry_fees_usd,COALESCE(safety_usd,0) AS safety_usd,(SELECT payload FROM paper_marks m WHERE m.position_id=paper_positions.id ORDER BY ts DESC,id DESC LIMIT 1) AS last_mark FROM paper_positions WHERE status='OPEN'"
            ) as c:
                rows = [dict(x) for x in await c.fetchall()]
                for row in rows:
                    row["last_mark"] = (
                        json.loads(row["last_mark"]) if row["last_mark"] else None
                    )
                return rows

    async def paper_stats(self):
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT COUNT(*),COALESCE(SUM(current_net_usd),0),COALESCE(SUM(CASE WHEN current_net_usd>0 THEN 1 ELSE 0 END),0) FROM paper_positions WHERE status='CLOSED'"
            ) as c:
                return await c.fetchone()

    async def record_execution_event(self, event):
        row = event.row()
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "INSERT INTO execution_events(trade_id,ts,kind,venue,symbol,side,qty,price,fee,reason,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["trade_id"],
                    row["ts"],
                    row["kind"],
                    row["venue"],
                    row["symbol"],
                    row["side"],
                    row["qty"],
                    row["price"],
                    row["fee"],
                    row["reason"],
                    json.dumps(row),
                ),
            )
            await d.commit()

    async def execution_history(self, trade_id):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT * FROM execution_events WHERE trade_id=? ORDER BY ts,id",
                (trade_id,),
            ) as cur:
                return [dict(x) for x in await cur.fetchall()]

    async def all_execution_events(self, limit=5000):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT trade_id,ts,kind,venue,symbol,side,qty,price,fee,reason,payload FROM execution_events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = [dict(x) for x in await cur.fetchall()]
        out = []
        for x in reversed(rows):
            try:
                p = json.loads(x.pop("payload") or "{}")
            except Exception:
                p = {}
            x.update(p)
            out.append(x)
        return out

    async def save_order_intent(self, intent, state=None):
        row = intent.row()
        row["state"] = state or row["state"]
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "INSERT INTO order_intents(intent_id,trade_id,venue,symbol,side,qty,reduce_only,state,updated_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,payload=excluded.payload",
                (
                    row["intent_id"],
                    row["trade_id"],
                    row["venue"],
                    row["symbol"],
                    row["side"],
                    row["qty"],
                    int(row["reduce_only"]),
                    row["state"],
                    time.time(),
                    json.dumps(row),
                ),
            )
            await d.commit()

    async def save_order_intent_result(self, intent, state, result):
        row = intent.row()
        row["state"] = state
        row.update(
            {
                "order_id": result.order_id,
                "exchange_status": result.status,
                "filled": result.filled,
                "avg_price": result.avg_price,
                "fee": result.fee,
                "base_fee": result.base_fee,
                "base_currency": result.base_currency,
            }
        )
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "INSERT INTO order_intents(intent_id,trade_id,venue,symbol,side,qty,reduce_only,state,updated_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,payload=excluded.payload",
                (
                    row["intent_id"],
                    row["trade_id"],
                    row["venue"],
                    row["symbol"],
                    row["side"],
                    row["qty"],
                    int(row["reduce_only"]),
                    row["state"],
                    time.time(),
                    json.dumps(row),
                ),
            )
            await d.commit()

    async def order_intent_states(self, trade_id=None):
        async with aiosqlite.connect(self.path) as d:
            q = "SELECT intent_id,state FROM order_intents"
            args = ()
            if trade_id is not None:
                q += " WHERE trade_id=?"
                args = (trade_id,)
            async with d.execute(q, args) as c:
                return {x[0]: x[1] for x in await c.fetchall()}

    async def order_intents(self, trade_id=None):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            q = "SELECT rowid AS _journal_sequence,* FROM order_intents"
            args = ()
            if trade_id is not None:
                q += " WHERE trade_id=?"
                args = (trade_id,)
            async with d.execute(q, args) as c:
                rows = [dict(x) for x in await c.fetchall()]
        out = {}
        for x in rows:
            sequence = x["_journal_sequence"]
            try:
                p = json.loads(x.get("payload") or "{}")
            except Exception:
                p = {}
            x.update(p)
            x["_journal_sequence"] = sequence
            out[x["intent_id"]] = x
        return out

    async def update_order_intent_reconciled(self, intent_id, state, result=None):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT payload FROM order_intents WHERE intent_id=?", (intent_id,)
            ) as cur:
                r = await cur.fetchone()
            if not r:
                return False
            try:
                p = json.loads(r["payload"] or "{}")
            except Exception:
                p = {}
            p["state"] = state
            valid_result = True
            if result is not None:
                from .order_settlement import valid

                valid_result = valid(
                    result, p.get("qty"), p.get("filled", 0), p.get("order_id")
                )
                if valid_result and p.get("base_currency") is not None:
                    valid_result = result.base_currency == p["base_currency"]
                    if result.filled == p.get("filled"):
                        valid_result = valid_result and (
                            result.base_fee == p.get("base_fee")
                            and result.fee == p.get("fee")
                        )
                if not valid_result:
                    state = "UNKNOWN"
                    p["state"] = state
                    result = None
            if result is not None:
                p.update(
                    {
                        "order_id": result.order_id,
                        "exchange_status": result.status,
                        "filled": result.filled,
                        "avg_price": result.avg_price,
                        "fee": result.fee,
                        "base_fee": result.base_fee,
                        "base_currency": result.base_currency,
                    }
                )
            await d.execute(
                "UPDATE order_intents SET state=?,updated_at=?,payload=? WHERE intent_id=?",
                (state, time.time(), json.dumps(p), intent_id),
            )
            await d.commit()
            return valid_result

    async def replay_trades(self, limit=500):
        async with aiosqlite.connect(self.path) as d:
            d.row_factory = aiosqlite.Row
            async with d.execute(
                "SELECT id,entry_spread,opened_at FROM (SELECT id,entry_spread,opened_at FROM paper_positions WHERE status='CLOSED' ORDER BY opened_at DESC LIMIT ?) ORDER BY opened_at ASC",
                (limit,),
            ) as c:
                ps = await c.fetchall()
            out = []
            for p in ps:
                async with d.execute(
                    "SELECT ts,net_usd,spread FROM paper_marks WHERE position_id=? ORDER BY ts",
                    (p["id"],),
                ) as c:
                    marks = await c.fetchall()
                if marks:
                    out.append(
                        (
                            p["entry_spread"],
                            [
                                (m["ts"] - p["opened_at"], m["net_usd"], m["spread"])
                                for m in marks
                            ],
                        )
                    )
            return out

    async def record_decisions(self, rows):
        if not rows:
            return
        async with aiosqlite.connect(self.path) as d:
            await d.executemany(
                "INSERT INTO signal_decisions(ts,strategy,symbol,buy,sell,action,reason,payload) VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        x["ts"],
                        x["strategy"],
                        x["symbol"],
                        x["buy"],
                        x["sell"],
                        x["action"],
                        x["reason"],
                        json.dumps(x),
                    )
                    for x in rows
                ],
            )
            await d.commit()

    async def claim_order_intent(self, intent, request=None):
        row = intent.row()
        row["state"] = "SUBMITTING"
        async with aiosqlite.connect(self.path) as d:
            c = await d.execute(
                "INSERT OR IGNORE INTO order_intents(intent_id,trade_id,venue,symbol,side,qty,reduce_only,state,updated_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    row["intent_id"],
                    row["trade_id"],
                    row["venue"],
                    row["symbol"],
                    row["side"],
                    row["qty"],
                    int(row["reduce_only"]),
                    "SUBMITTING",
                    time.time(),
                    json.dumps(row),
                ),
            )
            claimed = c.rowcount == 1
            if claimed and request is not None:
                from dataclasses import asdict

                await d.execute(
                    "INSERT INTO order_request_evidence(intent_id,trade_id,ts,payload) VALUES(?,?,?,?)",
                    (
                        intent.intent_id,
                        intent.trade_id,
                        time.time(),
                        json.dumps(asdict(request), allow_nan=False),
                    ),
                )
            await d.commit()
            return claimed

    async def order_request_evidence(self, intent_id):
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT payload FROM order_request_evidence WHERE intent_id=?",
                (intent_id,),
            ) as c:
                row = await c.fetchone()
                return json.loads(row[0]) if row else None
