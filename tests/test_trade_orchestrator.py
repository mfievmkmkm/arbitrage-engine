import asyncio
from app.mock_executor import MockExecutor
from app.trade_orchestrator import TradeOrchestrator
def test_orchestrator():
 async def go():
  o=TradeOrchestrator(MockExecutor(),MockExecutor())
  t=await o.enter("X","a","b",.01,.001,.01,round,round,2,.02,100,102)
  assert t.entry.hedged
  assert o.should_exit(t,.0141,now=t.exit_state.opened_at+1,target_capture=.7).close
 asyncio.run(go())
