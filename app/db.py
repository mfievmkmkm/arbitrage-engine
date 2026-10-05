import json, aiosqlite
SCHEMA="""
CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,ts REAL,symbol TEXT,buy TEXT,sell TEXT,raw REAL,executable REAL,fee_pct REAL,hypothetical_edge REAL,notional REAL,payload TEXT);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts);
CREATE TABLE IF NOT EXISTS paper_positions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,buy TEXT,sell TEXT,notional REAL,
 entry_buy REAL,entry_sell REAL,entry_spread REAL,opened_at REAL,best_net_usd REAL,
 current_net_usd REAL,current_spread REAL,status TEXT,closed_at REAL,close_reason TEXT);
CREATE TABLE IF NOT EXISTS paper_marks(id INTEGER PRIMARY KEY AUTOINCREMENT,position_id INTEGER,ts REAL,net_usd REAL,spread REAL);
"""
class Diary:
 def __init__(self,path):self.path=path
 async def init(self):
  async with aiosqlite.connect(self.path) as db:await db.executescript(SCHEMA);await db.commit()
 async def record(self,ops):
  if not ops:return
  async with aiosqlite.connect(self.path) as db:
   await db.executemany("INSERT INTO observations(ts,symbol,buy,sell,raw,executable,fee_pct,hypothetical_edge,notional,payload) VALUES(?,?,?,?,?,?,?,?,?,?)",[(o["ts"],o["symbol"],o["buy"],o["sell"],o["raw"],o["executable"],o["fee_pct"],o["hypothetical_edge"],o["notional"],json.dumps(o)) for o in ops]);await db.commit()
 async def summary(self):
  async with aiosqlite.connect(self.path) as db:
   async with db.execute("SELECT COUNT(*),MAX(ts),MAX(hypothetical_edge) FROM observations") as c:return await c.fetchone()
 async def create_paper_position(self,p):
  async with aiosqlite.connect(self.path) as db:
   c=await db.execute("INSERT INTO paper_positions(symbol,buy,sell,notional,entry_buy,entry_sell,entry_spread,opened_at,best_net_usd,current_net_usd,current_spread,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(p["symbol"],p["buy"],p["sell"],p["notional"],p["entry_buy"],p["entry_sell"],p["entry_spread"],p["opened_at"],p["best_net_usd"],p["current_net_usd"],p["current_spread"],p["status"]));await db.commit();return c.lastrowid
 async def update_paper_position(self,p):
  async with aiosqlite.connect(self.path) as db:
   await db.execute("UPDATE paper_positions SET best_net_usd=?,current_net_usd=?,current_spread=? WHERE id=?",(p["best_net_usd"],p["current_net_usd"],p["current_spread"],p["id"]));await db.execute("INSERT INTO paper_marks(position_id,ts,net_usd,spread) VALUES(?,?,?,?)",(p["id"],__import__("time").time(),p["current_net_usd"],p["current_spread"]));await db.commit()
 async def close_paper_position(self,p,reason):
  async with aiosqlite.connect(self.path) as db:await db.execute("UPDATE paper_positions SET status='CLOSED',closed_at=?,close_reason=?,best_net_usd=?,current_net_usd=?,current_spread=? WHERE id=?",(__import__("time").time(),reason,p["best_net_usd"],p["current_net_usd"],p["current_spread"],p["id"]));await db.commit()
 async def open_paper_positions(self):
  async with aiosqlite.connect(self.path) as db:
   db.row_factory=aiosqlite.Row
   async with db.execute("SELECT id,symbol,buy,sell,notional,entry_buy,entry_sell,entry_spread,opened_at,best_net_usd,current_net_usd,current_spread,status FROM paper_positions WHERE status='OPEN'") as c:return [dict(x) for x in await c.fetchall()]
 async def paper_stats(self):
  async with aiosqlite.connect(self.path) as db:
   async with db.execute("SELECT COUNT(*),COALESCE(SUM(current_net_usd),0),COALESCE(SUM(CASE WHEN current_net_usd>0 THEN 1 ELSE 0 END),0) FROM paper_positions WHERE status='CLOSED'") as c:return await c.fetchone()
