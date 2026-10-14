import aiosqlite,json
async def observations(path,limit=10000):
 async with aiosqlite.connect(path) as d:
  d.row_factory=aiosqlite.Row
  async with d.execute("SELECT * FROM strategy_observations ORDER BY ts DESC LIMIT ?",(limit,)) as c:
   out=[]
   for r in await c.fetchall():
    x=dict(r)
    try:x["payload"]=json.loads(x["payload"])
    except Exception:pass
    out.append(x)
   return out
async def ledger(path,limit=10000):
 async with aiosqlite.connect(path) as d:
  d.row_factory=aiosqlite.Row
  try:
   async with d.execute("SELECT * FROM ledger ORDER BY ts DESC LIMIT ?",(limit,)) as c:return [dict(x) for x in await c.fetchall()]
  except Exception:return []
