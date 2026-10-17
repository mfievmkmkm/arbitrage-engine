"""Independent durable notification queue; contains no execution authority."""
import json
import time
import aiosqlite


class Store:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS market_signal_queue(
                    key TEXT PRIMARY KEY, kind TEXT, ts REAL, payload TEXT,
                    delivered INTEGER DEFAULT 0, result TEXT);
                CREATE TABLE IF NOT EXISTS market_signal_settings(
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER);
                INSERT OR IGNORE INTO market_signal_settings VALUES(1,1);
            ''')
            await db.commit()

    async def enabled(self):
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute('SELECT enabled FROM market_signal_settings WHERE id=1')).fetchone()
        return bool(row[0])

    async def toggle(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE market_signal_settings SET enabled=1-enabled WHERE id=1')
            await db.commit()

    async def enqueue(self, key, kind, payload):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('INSERT OR IGNORE INTO market_signal_queue(key,kind,ts,payload) VALUES(?,?,?,?)',
                             (key, kind, self.clock(), json.dumps(payload, allow_nan=False)))
            await db.commit()

    async def pending(self):
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute('SELECT key,kind,ts,payload,result FROM market_signal_queue WHERE delivered=0 ORDER BY ts,key LIMIT 10')).fetchall()
        return [dict(key=k, kind=t, ts=ts, payload=json.loads(p), result=r) for k,t,ts,p,r in rows]

    async def result(self, key, text):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE market_signal_queue SET result=? WHERE key=?', (text, key))
            await db.commit()

    async def delivered(self, key):
        async with aiosqlite.connect(self.path) as db:
            await db.execute('UPDATE market_signal_queue SET delivered=1 WHERE key=?', (key,))
            await db.commit()
