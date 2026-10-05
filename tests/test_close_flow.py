import asyncio
from app.close_flow import close_trade
from app.runtime_state import RuntimeTrade
from app.mock_executor import MockExecutor
def test_close_flow():
 async def go():
  t=RuntimeTrade("1","X","a","b",1,1,1,1,1,100,110,0)
  r,x,s=await close_trade(t,MockExecutor(),MockExecutor(),104,106)
  assert s=="CLOSED" and x.flat and r.net==8
 asyncio.run(go())
