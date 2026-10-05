import asyncio
from app.mock_executor import MockExecutor
from app.recovery_flow import recover
def test_complete_recovery():
 async def go():
  x=await recover("X","a","b",.01,.005,.001,.01,MockExecutor(),MockExecutor(),1,.01,.02)
  assert x.action=="COMPLETE" and x.completed
 asyncio.run(go())
