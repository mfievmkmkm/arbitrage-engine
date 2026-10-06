import asyncio
from app.trade_orchestrator import TradeOrchestrator
from app.mock_executor import MockExecutor
from app.exchange_executor import SubmitResult

class FeeExecutor(MockExecutor):
 def __init__(self,price,fee):super().__init__(1,price);self.fee=fee
 async def submit(self,r):
  x=await super().submit(r)
  return SubmitResult(x.order_id,x.status,x.filled,x.avg_price,self.fee)

def test_exit_target_uses_actual_fill_edge_minus_entry_fees():
 async def go():
  o=TradeOrchestrator(FeeExecutor(101,.25),FeeExecutor(109,.25))
  t=await o.enter("X","a","b",1,1,1,round,round,99,.01,100,110)
  assert abs(t.exit_state.entry_net_edge-7.5)<1e-12
 asyncio.run(go())
