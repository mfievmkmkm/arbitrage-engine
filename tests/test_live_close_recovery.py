import asyncio
from app.live_close_flow import close_verified
from app.mock_executor import MockExecutor
from app.runtime_state import RuntimeTrade

class H:ok=True
class PartialOnce(MockExecutor):
 def __init__(self,price):
  super().__init__(.5,price);self.calls=0
 async def submit(self,r):
  self.calls+=1
  if self.calls>1:self.fill_ratio=1
  return await super().submit(r)

def test_partial_long_close_is_recovered_then_private_verified():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,2,2,.5,.5,100,110,0)
  le=PartialOnce(104);se=MockExecutor(price=106)
  async def snapshot():
   return {"a":{"health":H(),"positions":[]},"b":{"health":H(),"positions":[]}}
  x=await close_verified(t,le,se,snapshot,104,106,private_attempts=1,private_delay=0)
  assert x.status=="CLOSED"
  assert le.calls==2 and x.execution.flat
 asyncio.run(go())

def test_both_partial_legs_fail_closed_for_reconcile():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,2,2,.5,.5,100,110,0)
  async def snapshot():
   return {"a":{"health":H(),"positions":[]},"b":{"health":H(),"positions":[]}}
  x=await close_verified(t,MockExecutor(.5,104),MockExecutor(.5,106),snapshot,104,106,private_attempts=1,private_delay=0)
  assert x.status=="CLOSE_RECOVERY_FAILED_BOTH_LEGS_RESIDUAL_RECONCILE"
 asyncio.run(go())
