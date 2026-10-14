import asyncio
from app.trade_orchestrator import TradeOrchestrator
from app.mock_executor import MockExecutor
def test_partial_entry_recovery():
 async def go():
  long=MockExecutor(1);short=MockExecutor(.5)
  o=TradeOrchestrator(long,short)
  try:
   t=await o.enter("X","a","b",.01,.001,.01,round,round,2,.02,100,102)
  except RuntimeError:
   return
  assert t.lifecycle.phase.value=="HEDGED"
 asyncio.run(go())
