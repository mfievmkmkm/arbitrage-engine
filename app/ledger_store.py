import aiosqlite
SCHEMA="CREATE TABLE IF NOT EXISTS ledger(ts REAL,kind TEXT,trade_id TEXT,venue TEXT,amount REAL,note TEXT); CREATE INDEX IF NOT EXISTS idx_ledger_kind_ts ON ledger(kind,ts);"
async def init(path):
 async with aiosqlite.connect(path) as d:await d.executescript(SCHEMA);await d.commit()
async def add(path,ts,kind,amount,trade_id="",venue="",note=""):
 async with aiosqlite.connect(path) as d:await d.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?)",(ts,kind,trade_id,venue,amount,note));await d.commit()
async def totals(path):
 async with aiosqlite.connect(path) as d:
  async with d.execute("SELECT kind,COALESCE(SUM(amount),0) FROM ledger GROUP BY kind") as c:return dict(await c.fetchall())
