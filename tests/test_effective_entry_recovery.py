import asyncio
from app.trade_orchestrator import TradeOrchestrator
from app.mock_executor import MockExecutor
from app.actual_entry import build

class PartialLong(MockExecutor):
 def __init__(self):super().__init__(.5,100);self.calls=0
 async def submit(self,r):
  self.calls+=1
  if self.calls>1:self.fill_ratio=1
  return await super().submit(r)

class PartialShort(MockExecutor):
 def __init__(self):super().__init__(.5,110);self.calls=0
 async def submit(self,r):
  self.calls+=1
  if self.calls>1:self.fill_ratio=1
  return await super().submit(r)

def test_recovered_entry_uses_final_actual_fills():
 async def go():
  le=MockExecutor(1,100);se=PartialShort()
  o=TradeOrchestrator(le,se)
  t=await o.enter("X","a","b",1,1,1,round,round,10,.01,100,110)
  assert t.recovery and t.recovery.completed
  assert t.entry.hedged
  a=build(t.entry,t.plan)
  assert abs(a.base_qty-1)<1e-12
  assert abs(a.long_price-100)<1e-12
  assert abs(a.short_price-110)<1e-12
 asyncio.run(go())
