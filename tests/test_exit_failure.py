import asyncio
from app.exit_runner import run
from app.mock_executor import MockExecutor
class Bad(MockExecutor):
 async def submit(self,r):raise RuntimeError("boom")
def test_partial_exit_never_flat():
 async def go():
  x=await run("X",1,1,1,1,MockExecutor(),Bad())
  assert not x.flat and x.short_error=="RuntimeError" and x.long_result.filled==1
 asyncio.run(go())
