import json
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
 symbol TEXT NOT NULL, buy TEXT NOT NULL, sell TEXT NOT NULL,
 raw REAL NOT NULL, executable REAL NOT NULL,
 fee_pct REAL NOT NULL, hypothetical_edge REAL NOT NULL,
 notional REAL NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts);
"""

class Diary:
    def __init__(self, path):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def record(self, opportunities):
        if not opportunities:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                """INSERT INTO observations
                (ts,symbol,buy,sell,raw,executable,fee_pct,
                 hypothetical_edge,notional,payload)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(o["ts"], o["symbol"], o["buy"], o["sell"], o["raw"],
                  o["executable"], o["fee_pct"], o["hypothetical_edge"],
                  o["notional"], json.dumps(o)) for o in opportunities])
            await db.commit()

    async def summary(self):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT COUNT(*), MAX(ts), MAX(hypothetical_edge) FROM observations"
            ) as cur:
                return await cur.fetchone()

    async def recent(self, limit=8):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """SELECT symbol,buy,sell,raw,executable,hypothetical_edge,ts
                FROM observations ORDER BY ts DESC LIMIT ?""", (limit,)
            ) as cur:
                return await cur.fetchall()
