import asyncio
from app.two_leg_runner import run
from app.executor_plan import build
from app.order_policy import OrderPolicy
from app.mock_executor import MockExecutor
class Bad(MockExecutor):
 async def submit(self,r):raise RuntimeError("boom")
def test_one_leg_failure():
 async def go():
  p=build("X","a","b",.01,.001,.01,round,round)
  x=await run(p,MockExecutor(),Bad(),OrderPolicy("market",False,"URGENT"))
  assert not x.hedged and x.short_error=="RuntimeError" and x.long_result.filled>0
 asyncio.run(go())
