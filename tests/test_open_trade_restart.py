import asyncio
from app.open_trade_restart import recover
from app.runtime_store import RuntimeStore
from app.runtime_state import RuntimeTrade
from app.db import Diary
class H:ok=True
def test_restart_retains_open_trade_without_reentry(tmp_path):
 async def go():
  store=RuntimeStore(str(tmp_path/"r.json"));t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0);store.save([t])
  d=Diary(str(tmp_path/"d.db"));await d.init()
  snap={"a":{"health":H(),"positions":[],"orders":[]},"b":{"health":H(),"positions":[],"orders":[]}}
  x=await recover(store,d,{},snap)
  assert x.safe and x.action=="RESUME_OPEN_TRADES" and x.trades[0].trade_id=="t"
 asyncio.run(go())
