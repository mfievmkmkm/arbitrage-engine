import asyncio
from app.exit_runner import run
from app.mock_executor import MockExecutor
def test_close():
 async def go():
  x=await run("X",10,1,.001,.01,MockExecutor(),MockExecutor())
  assert x.flat
 asyncio.run(go())
