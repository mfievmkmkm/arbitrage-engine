import asyncio
from app.runtime_engine import RuntimeEngine
from app.runtime_store import RuntimeStore
from app.trade_orchestrator import TradeOrchestrator
from app.mock_executor import MockExecutor
class S:contract_size=.001
class T:contract_size=.01
def test_runtime(tmp_path):
 async def go():
  e=RuntimeEngine(TradeOrchestrator(MockExecutor(),MockExecutor()),RuntimeStore(str(tmp_path/"s")),1,50)
  op={"symbol":"X","buy":"a","sell":"b","notional":5,"hypothetical_edge":2,"funding_known":True,"raw":2.1,"executable":2}
  x,r=await e.consider(op,S(),T(),round,round,100,102)
  assert r=="OPENED" and len(e.trades)==1
 async def wrap():await go()
 asyncio.run(wrap())
