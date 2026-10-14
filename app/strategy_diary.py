import json,time,aiosqlite
SCHEMA="CREATE TABLE IF NOT EXISTS strategy_observations(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL,strategy TEXT,symbol TEXT,venue TEXT,edge REAL,notional REAL,payload TEXT); CREATE INDEX IF NOT EXISTS idx_strategy_obs ON strategy_observations(strategy,ts);"
async def init(path):
 async with aiosqlite.connect(path) as d:await d.executescript(SCHEMA);await d.commit()
async def record(path,rows):
 if not rows:return
 async with aiosqlite.connect(path) as d:await d.executemany("INSERT INTO strategy_observations(ts,strategy,symbol,venue,edge,notional,payload) VALUES(?,?,?,?,?,?,?)",[(r["ts"],r["strategy"],r["symbol"],r["venue"],r["edge"],r["notional"],json.dumps(r["payload"])) for r in rows]);await d.commit()
async def summary(path):
 async with aiosqlite.connect(path) as d:
  d.row_factory=aiosqlite.Row
  async with d.execute("SELECT strategy,COUNT(*) observations,AVG(edge) avg_edge,MAX(edge) best_edge FROM strategy_observations GROUP BY strategy") as c:return [dict(x) for x in await c.fetchall()]
