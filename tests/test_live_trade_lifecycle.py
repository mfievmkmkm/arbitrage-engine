import asyncio
from app.live_trade_lifecycle import close
from app.runtime_state import RuntimeTrade
from app.mock_executor import MockExecutor
from app.private_adapter import PrivatePosition

class Safe:
 def __init__(self,inner):self.inner=inner;self.intents=[]
 async def submit_intent(self,i,r):self.intents.append((i,r));return await self.inner.submit(r),"FILLED"
 async def submit(self,r):return await self.inner.submit(r)
class H:ok=True
def test_full_close_persists_reduce_only_intents_and_requires_private_flat():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0,entry_fees=.1)
  a=Safe(MockExecutor(1,105));b=Safe(MockExecutor(1,105))
  snap=lambda:{"a":{"health":H(),"positions":[]},"b":{"health":H(),"positions":[]}}
  x=await close(t,a,b,snap,attempts=1,delay=0)
  assert x.closed and x.status=="CLOSED_VERIFIED" and x.result is not None
  assert a.intents[0][0].reduce_only and b.intents[0][0].reduce_only
  assert a.intents[0][1].client_order_id=="t:exit-long"
 async def blocked():
  t=RuntimeTrade("t2","X","a","b",1,1,1,1,1,100,110,0)
  a=Safe(MockExecutor(1,105));b=Safe(MockExecutor(1,105))
  snap=lambda:{"a":{"health":H(),"positions":[PrivatePosition("a","X","long",1)]},"b":{"health":H(),"positions":[]}}
  x=await close(t,a,b,snap,attempts=1,delay=0)
  assert not x.closed and x.status.startswith("CLOSE_UNVERIFIED")
 asyncio.run(go());asyncio.run(blocked())
