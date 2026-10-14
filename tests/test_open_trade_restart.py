import asyncio
from app.open_trade_restart import recover
from app.runtime_store import RuntimeStore
from app.runtime_state import RuntimeTrade
from app.db import Diary
from app.private_adapter import PrivatePosition
class H:ok=True
def test_restart_retains_open_trade_without_reentry(tmp_path):
 async def go():
  store=RuntimeStore(str(tmp_path/"r.json"));t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0);store.save([t])
  d=Diary(str(tmp_path/"d.db"));await d.init()
  snap={"a":{"health":H(),"positions":[PrivatePosition("a","X","long",1)],"orders":[]},"b":{"health":H(),"positions":[PrivatePosition("b","X","short",1)],"orders":[]}}
  x=await recover(store,d,{},snap)
  assert x.safe and x.action=="RESUME_OPEN_TRADES" and x.trades[0].trade_id=="t"
 asyncio.run(go())
