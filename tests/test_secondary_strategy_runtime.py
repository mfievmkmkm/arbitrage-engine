import asyncio
from app.secondary_strategy_runtime import SecondaryRuntime
from app.strategy_runtime import StrategyRuntime
class S:
 async def cycle(self):return [{"strategy":"x","symbol":"X","net":2}]
def test_secondary_runtime_updates_and_persists():
 async def go():
  seen=[]
  async def rec(path,rows):seen.extend(rows)
  r=StrategyRuntime();x=SecondaryRuntime(r,rec,"x",.01);x.add("x",S());await x.start();await asyncio.sleep(.03);await x.stop();assert r.counts()["x"] and seen
 asyncio.run(go())
