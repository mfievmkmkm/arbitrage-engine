import json,aiosqlite
SCHEMA="CREATE TABLE IF NOT EXISTS spot_future_paper(id INTEGER PRIMARY KEY,base TEXT,exchange TEXT,direction TEXT,notional REAL,base_qty REAL,spot_entry REAL,future_entry REAL,opened_at REAL,best_net REAL,net REAL,status TEXT,payload TEXT);"
async def init(path):
 async with aiosqlite.connect(path) as d:await d.execute(SCHEMA);await d.commit()
async def save(path,p):
 async with aiosqlite.connect(path) as d:
  await d.execute("INSERT OR REPLACE INTO spot_future_paper VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(p.id,p.base,p.exchange,p.direction,p.notional,p.base_qty,p.spot_entry,p.future_entry,p.opened_at,p.best_net,p.net,p.status,json.dumps(p.__dict__)));await d.commit()
async def open_rows(path):
 async with aiosqlite.connect(path) as d:
  d.row_factory=aiosqlite.Row
  async with d.execute("SELECT * FROM spot_future_paper WHERE status='OPEN'") as c:return [dict(x) for x in await c.fetchall()]
