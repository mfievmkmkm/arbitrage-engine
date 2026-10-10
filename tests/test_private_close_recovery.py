import asyncio
from app.live_close_flow import close_verified
from app.mock_executor import MockExecutor
from app.runtime_state import RuntimeTrade
from app.private_adapter import PrivatePosition

class H:ok=True
class FirstPartial(MockExecutor):
 def __init__(self,price):
  super().__init__(.5,price);self.calls=0
 async def submit(self,r):
  self.calls+=1
  if self.calls>1:self.fill_ratio=1
  return await super().submit(r)

def test_dual_partial_recovers_from_private_positions():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,2,2,.5,.5,100,110,0)
  le=FirstPartial(104);se=FirstPartial(106);calls={"n":0}
  async def snapshot():
   calls["n"]+=1
   if calls["n"]==1:
    return {
     "a":{"health":H(),"positions":[PrivatePosition("a","X","long",.5,contracts=1,contract_size=.5)]},
     "b":{"health":H(),"positions":[PrivatePosition("b","X","short",.5,contracts=1,contract_size=.5)]},
    }
   return {"a":{"health":H(),"positions":[]},"b":{"health":H(),"positions":[]}}
  x=await close_verified(t,le,se,snapshot,104,106,private_attempts=2,private_delay=0)
  assert x.status=="CLOSED_RECOVERED_PRIVATE"
  assert x.execution.long_result.filled==2 and x.execution.short_result.filled==2
  assert le.calls==2 and se.calls==2
 asyncio.run(go())

def test_dual_partial_refuses_flipped_private_exposure():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,2,2,.5,.5,100,110,0)
  async def snapshot():
   return {
    "a":{"health":H(),"positions":[PrivatePosition("a","X","short",.5,contracts=1,contract_size=.5)]},
    "b":{"health":H(),"positions":[PrivatePosition("b","X","short",.5,contracts=1,contract_size=.5)]},
   }
  x=await close_verified(t,MockExecutor(.5,104),MockExecutor(.5,106),snapshot,104,106,private_attempts=1,private_delay=0)
  assert x.status=="CLOSE_RECOVERY_FAILED_OPPOSITE_OR_FLIPPED_EXPOSURE"
 asyncio.run(go())
