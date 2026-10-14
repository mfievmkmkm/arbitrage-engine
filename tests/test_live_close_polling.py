import asyncio
from app.live_close_flow import close_verified
from app.mock_executor import MockExecutor
from app.runtime_state import RuntimeTrade
from app.private_adapter import PrivatePosition

class H:ok=True

def test_close_waits_for_fresh_private_flat():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0)
  calls={"n":0}
  async def snapshot():
   calls["n"]+=1
   if calls["n"]==1:
    return {"a":{"health":H(),"positions":[PrivatePosition("a","X","long",1)]},"b":{"health":H(),"positions":[]}}
   return {"a":{"health":H(),"positions":[]},"b":{"health":H(),"positions":[]}}
  x=await close_verified(t,MockExecutor(price=104),MockExecutor(price=106),snapshot,104,106,private_attempts=2,private_delay=0)
  assert x.status=="CLOSED" and calls["n"]==2
 asyncio.run(go())

def test_close_rejects_flipped_private_position():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0)
  async def snapshot():
   return {"a":{"health":H(),"positions":[PrivatePosition("a","X","short",1)]},"b":{"health":H(),"positions":[]}}
  x=await close_verified(t,MockExecutor(price=104),MockExecutor(price=106),snapshot,104,106,private_attempts=1,private_delay=0)
  assert x.status=="CLOSE_UNVERIFIED_OPPOSITE_OR_FLIPPED_EXPOSURE"
 asyncio.run(go())
