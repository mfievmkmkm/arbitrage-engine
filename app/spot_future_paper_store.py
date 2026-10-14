import json, aiosqlite, time

SCHEMA = "CREATE TABLE IF NOT EXISTS spot_future_paper(id INTEGER PRIMARY KEY,base TEXT,exchange TEXT,direction TEXT,notional REAL,base_qty REAL,spot_entry REAL,future_entry REAL,opened_at REAL,best_net REAL,net REAL,status TEXT,payload TEXT);"


async def init(path):
    async with aiosqlite.connect(path) as d:
        await d.execute(SCHEMA)
        async with d.execute("PRAGMA table_info(spot_future_paper)") as c:
            columns = {x[1] for x in await c.fetchall()}
        if "closed_at" not in columns:
            await d.execute("ALTER TABLE spot_future_paper ADD COLUMN closed_at REAL")
        await d.execute(
            "CREATE TABLE IF NOT EXISTS spot_future_marks(id INTEGER PRIMARY KEY,position_id INTEGER,ts REAL,net REAL)"
        )
        async with d.execute("PRAGMA table_info(spot_future_marks)") as c:
            mark_columns = {x[1] for x in await c.fetchall()}
        if "payload" not in mark_columns:
            await d.execute("ALTER TABLE spot_future_marks ADD COLUMN payload TEXT")
        await d.commit()


async def save(path, p):
    async with aiosqlite.connect(path) as d:
        await d.execute(
            """INSERT INTO spot_future_paper(id,base,exchange,direction,notional,base_qty,spot_entry,future_entry,opened_at,best_net,net,status,payload,closed_at)
   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET best_net=excluded.best_net,net=excluded.net,status=excluded.status,payload=excluded.payload,closed_at=COALESCE(spot_future_paper.closed_at,excluded.closed_at)""",
            (
                p.id,
                p.base,
                p.exchange,
                p.direction,
                p.notional,
                p.base_qty,
                p.spot_entry,
                p.future_entry,
                p.opened_at,
                p.best_net,
                p.net,
                p.status,
                json.dumps(p.__dict__),
                time.time() if p.status != "OPEN" else None,
            ),
        )
        await d.commit()


async def open_rows(path):
    async with aiosqlite.connect(path) as d:
        d.row_factory = aiosqlite.Row
        async with d.execute(
            "SELECT * FROM spot_future_paper WHERE status='OPEN'"
        ) as c:
            return [dict(x) for x in await c.fetchall()]


async def all_rows(path):
    async with aiosqlite.connect(path) as d:
        d.row_factory = aiosqlite.Row
        async with d.execute("SELECT * FROM spot_future_paper ORDER BY id") as c:
            return [dict(x) for x in await c.fetchall()]


async def mark(path, p, ts):
    async with aiosqlite.connect(path) as d:
        await d.execute(
            "INSERT INTO spot_future_marks(position_id,ts,net,payload) VALUES(?,?,?,?)",
            (
                p.id,
                (p.last_mark or {}).get("ts", ts),
                p.net,
                json.dumps(p.last_mark) if p.last_mark else None,
            ),
        )
        await d.commit()
