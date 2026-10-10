import asyncio
from app.strategy_orchestrator import StrategyOrchestrator
from app.unified_runtime import UnifiedRuntime
class S:
 async def scan(self):return [{"strategy":"spot_futures","ts":1,"base":"X","exchange":"a","hypothetical_edge":2,"notional":5}]
def test_orchestrator_combines_strategies():
 async def go():
  r=UnifiedRuntime();x=await StrategyOrchestrator(r,S()).cycle([{"strategy":"futures_futures"}]);assert len(x["spot_futures"])==1 and r.snapshot()["counts"]["futures_futures"]==1
 asyncio.run(go())
