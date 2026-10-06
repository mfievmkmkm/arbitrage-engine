import json,aiosqlite,time
SCHEMA="""CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,ts REAL,symbol TEXT,buy TEXT,sell TEXT,raw REAL,executable REAL,fee_pct REAL,hypothetical_edge REAL,notional REAL,payload TEXT);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations(ts);
CREATE TABLE IF NOT EXISTS paper_positions(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT,buy TEXT,sell TEXT,notional REAL,entry_buy REAL,entry_sell REAL,entry_spread REAL,opened_at REAL,best_net_usd REAL,current_net_usd REAL,current_spread REAL,status TEXT,closed_at REAL,close_reason TEXT);
CREATE TABLE IF NOT EXISTS paper_marks(id INTEGER PRIMARY KEY AUTOINCREMENT,position_id INTEGER,ts REAL,net_usd REAL,spread REAL);\nCREATE TABLE IF NOT EXISTS execution_events(id INTEGER PRIMARY KEY AUTOINCREMENT,trade_id TEXT,ts REAL,kind TEXT,venue TEXT,symbol TEXT,side TEXT,qty REAL,price REAL,fee REAL,reason TEXT,payload TEXT);\nCREATE INDEX IF NOT EXISTS idx_execution_trade ON execution_events(trade_id,ts);\nCREATE TABLE IF NOT EXISTS order_intents(intent_id TEXT PRIMARY KEY,trade_id TEXT,venue TEXT,symbol TEXT,side TEXT,qty REAL,reduce_only INTEGER,state TEXT,updated_at REAL,payload TEXT);"""
class Diary:
 def __init__(self,path):self.path=path
 async def init(self):
  async with aiosqlite.connect(self.path) as d:await d.executescript(SCHEMA);await d.commit()
 async def record(self,ops):
  if not ops:return
  async with aiosqlite.connect(self.path) as d:await d.executemany("INSERT INTO observations(ts,symbol,buy,sell,raw,executable,fee_pct,hypothetical_edge,notional,payload) VALUES(?,?,?,?,?,?,?,?,?,?)",[(o["ts"],o["symbol"],o["buy"],o["sell"],o["raw"],o["executable"],o["fee_pct"],o["hypothetical_edge"],o["notional"],json.dumps(o)) for o in ops]);await d.commit()
 async def summary(self):
  async with aiosqlite.connect(self.path) as d:
   async with d.execute("SELECT COUNT(*),MAX(ts),MAX(hypothetical_edge) FROM observations") as c:return await c.fetchone()
 async def create_paper_position(self,p):
  async with aiosqlite.connect(self.path) as d:
   c=await d.execute("INSERT INTO paper_positions(symbol,buy,sell,notional,entry_buy,entry_sell,entry_spread,opened_at,best_net_usd,current_net_usd,current_spread,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(p["symbol"],p["buy"],p["sell"],p["notional"],p["entry_buy"],p["entry_sell"],p["entry_spread"],p["opened_at"],p["best_net_usd"],p["current_net_usd"],p["current_spread"],p["status"]));await d.commit();return c.lastrowid
 async def update_paper_position(self,p):
  async with aiosqlite.connect(self.path) as d:await d.execute("UPDATE paper_positions SET best_net_usd=?,current_net_usd=?,current_spread=? WHERE id=?",(p["best_net_usd"],p["current_net_usd"],p["current_spread"],p["id"]));await d.execute("INSERT INTO paper_marks(position_id,ts,net_usd,spread) VALUES(?,?,?,?)",(p["id"],time.time(),p["current_net_usd"],p["current_spread"]));await d.commit()
 async def close_paper_position(self,p,reason):
  async with aiosqlite.connect(self.path) as d:await d.execute("UPDATE paper_positions SET status='CLOSED',closed_at=?,close_reason=?,best_net_usd=?,current_net_usd=?,current_spread=? WHERE id=?",(time.time(),reason,p["best_net_usd"],p["current_net_usd"],p["current_spread"],p["id"]));await d.commit()
 async def open_paper_positions(self):
  async with aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row
   async with d.execute("SELECT id,symbol,buy,sell,notional,entry_buy,entry_sell,entry_spread,opened_at,best_net_usd,current_net_usd,current_spread,status FROM paper_positions WHERE status='OPEN'") as c:return [dict(x) for x in await c.fetchall()]
 async def paper_stats(self):
  async with aiosqlite.connect(self.path) as d:
   async with d.execute("SELECT COUNT(*),COALESCE(SUM(current_net_usd),0),COALESCE(SUM(CASE WHEN current_net_usd>0 THEN 1 ELSE 0 END),0) FROM paper_positions WHERE status='CLOSED'") as c:return await c.fetchone()
 async def record_execution_event(self,event):
  row=event.row()
  async with aiosqlite.connect(self.path) as d:
   await d.execute("INSERT INTO execution_events(trade_id,ts,kind,venue,symbol,side,qty,price,fee,reason,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(row["trade_id"],row["ts"],row["kind"],row["venue"],row["symbol"],row["side"],row["qty"],row["price"],row["fee"],row["reason"],json.dumps(row)))
   await d.commit()
 async def execution_history(self,trade_id):
  async with aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row
   async with d.execute("SELECT * FROM execution_events WHERE trade_id=? ORDER BY ts,id",(trade_id,)) as cur:return [dict(x) for x in await cur.fetchall()]
 async def all_execution_events(self,limit=5000):
  async with aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row
   async with d.execute("SELECT trade_id,ts,kind,venue,symbol,side,qty,price,fee,reason,payload FROM execution_events ORDER BY ts DESC LIMIT ?",(limit,)) as cur:
    rows=[dict(x) for x in await cur.fetchall()]
  out=[]
  for x in reversed(rows):
   try:p=json.loads(x.pop("payload") or "{}")
   except Exception:p={}
   x.update(p);out.append(x)
  return out
 async def save_order_intent(self,intent,state=None):
  row=intent.row();row["state"]=state or row["state"]
  async with aiosqlite.connect(self.path) as d:
   await d.execute("INSERT INTO order_intents(intent_id,trade_id,venue,symbol,side,qty,reduce_only,state,updated_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,payload=excluded.payload",(row["intent_id"],row["trade_id"],row["venue"],row["symbol"],row["side"],row["qty"],int(row["reduce_only"]),row["state"],time.time(),json.dumps(row)))
   await d.commit()
 async def order_intent_states(self,trade_id=None):
  async with aiosqlite.connect(self.path) as d:
   q="SELECT intent_id,state FROM order_intents";args=()
   if trade_id is not None:q+=" WHERE trade_id=?";args=(trade_id,)
   async with d.execute(q,args) as c:return {x[0]:x[1] for x in await c.fetchall()}
 async def order_intents(self,trade_id=None):
  async with aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row;q="SELECT * FROM order_intents";args=()
   if trade_id is not None:q+=" WHERE trade_id=?";args=(trade_id,)
   async with d.execute(q,args) as c:
    rows=[dict(x) for x in await c.fetchall()]
  out={}
  for x in rows:
   try:p=json.loads(x.get("payload") or "{}")
   except Exception:p={}
   x.update(p);out[x["intent_id"]]=x
  return out
 async def replay_trades(self,limit=500):
  async with aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row
   async with d.execute("SELECT id,entry_spread,opened_at FROM (SELECT id,entry_spread,opened_at FROM paper_positions WHERE status='CLOSED' ORDER BY opened_at DESC LIMIT ?) ORDER BY opened_at ASC",(limit,)) as c:ps=await c.fetchall()
   out=[]
   for p in ps:
    async with d.execute("SELECT ts,net_usd,spread FROM paper_marks WHERE position_id=? ORDER BY ts",(p["id"],)) as c:marks=await c.fetchall()
    if marks:out.append((p["entry_spread"],[(m["ts"]-p["opened_at"],m["net_usd"],m["spread"]) for m in marks]))
   return out
