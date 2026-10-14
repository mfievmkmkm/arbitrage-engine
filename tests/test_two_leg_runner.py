import asyncio
from app.executor_plan import build
from app.mock_executor import MockExecutor
from app.order_policy import choose
from app.two_leg_runner import run
def test_two_leg_mock():
 async def go():
  p=build("X","a","b",.01,.001,.01,lambda x:round(x),lambda x:round(x))
  x=await run(p,MockExecutor(),MockExecutor(),choose(2,.02,True),100,102)
  assert x.hedged
 asyncio.run(go())
