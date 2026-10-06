import aiosqlite,json,time
SCHEMA="""CREATE TABLE IF NOT EXISTS live_trades(trade_id TEXT PRIMARY KEY,phase TEXT NOT NULL,symbol TEXT,long_venue TEXT,short_venue TEXT,planned_long REAL,planned_short REAL,actual_long REAL DEFAULT 0,actual_short REAL DEFAULT 0,long_price REAL,short_price REAL,fees REAL DEFAULT 0,updated_at REAL NOT NULL,payload TEXT NOT NULL DEFAULT '{}')"""
class Store:
 def __init__(self,path):self.path=path
 async def init(self):
  async with aiosqlite.connect(self.path) as d:await d.execute(SCHEMA);await d.commit()
 async def phase(self,trade_id,phase,**x):
  now=time.time();payload=json.dumps(x,separators=(",",":"))
  async with aiosqlite.connect(self.path) as d:
   await d.execute("INSERT INTO live_trades(trade_id,phase,symbol,long_venue,short_venue,planned_long,planned_short,updated_at,payload) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_id) DO UPDATE SET phase=excluded.phase,updated_at=excluded.updated_at,payload=excluded.payload",(trade_id,phase,x.get("symbol"),x.get("long_venue"),x.get("short_venue"),x.get("planned_long"),x.get("planned_short"),now,payload));await d.commit()
 async def get(self,trade_id):\n  async with aiosqlite.connect(self.path) as d:\n   d.row_factory=aiosqlite.Row\n   async with d.execute("SELECT * FROM live_trades WHERE trade_id=?",(trade_id,)) as c:\n    x=await c.fetchone();return dict(x) if x else None\n async def active(self):
  async with aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row
   async with d.execute("SELECT * FROM live_trades WHERE phase NOT IN ('CLOSED_PRIVATE_VERIFIED','ABORTED') ORDER BY updated_at") as c:return [dict(x) for x in await c.fetchall()]
