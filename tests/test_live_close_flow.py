import asyncio
from app.live_close_flow import close_verified
from app.mock_executor import MockExecutor
from app.runtime_state import RuntimeTrade
def test_unverified_not_closed():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0)
  x=await close_verified(t,MockExecutor(),MockExecutor(),{},100,100)
  assert x.status.startswith("CLOSE_UNVERIFIED")
 asyncio.run(go())
