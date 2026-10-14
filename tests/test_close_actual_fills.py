import asyncio
from app.close_flow import close_trade
from app.mock_executor import MockExecutor
from app.runtime_state import RuntimeTrade
def test_actual_exit_prices():
 async def go():
  t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0)
  r,x,s=await close_trade(t,MockExecutor(price=104),MockExecutor(price=106),999,999)
  assert s=="CLOSED" and r.gross==8
 asyncio.run(go())
