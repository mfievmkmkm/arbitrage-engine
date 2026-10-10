import asyncio
from types import SimpleNamespace
from app.post_fill_enforcer import enforce
from app.runtime_state import RuntimeTrade
from app.executor_plan import build
from app.mock_executor import MockExecutor
class Safe:
 def __init__(self,x):self.x=x;self.intents=[]
 async def submit_intent(self,i,r):self.intents.append(i);return await self.x.submit(r),"FILLED"
def test_bad_actual_entry_emergency_flattens_both_legs():
 async def go():
  p=build("X","a","b",1,1,1,lambda x:x,lambda x:x)
  t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,100.1,0)
  actual=SimpleNamespace(base_qty=1,long_price=100,short_price=100.1,long_fee=.1,short_fee=.1)
  a=Safe(MockExecutor(1,100));b=Safe(MockExecutor(1,100))
  x=await enforce(actual,p,t,a,b,.1,.05)
  assert x.flattened and len(a.intents)==1 and len(b.intents)==1
 asyncio.run(go())
