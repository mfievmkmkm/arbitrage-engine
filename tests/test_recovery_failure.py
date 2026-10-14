import asyncio
from app.recovery_flow import recover
from app.mock_executor import MockExecutor
class Bad(MockExecutor):
 async def submit(self,r):raise RuntimeError("boom")
def test_recovery_failure():
 async def go():
  x=await recover("X","a","b",1,0,1,1,Bad(),MockExecutor(),1,2,1)
  assert not x.completed and x.error=="RuntimeError"
 asyncio.run(go())
