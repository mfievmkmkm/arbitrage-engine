import time,uuid
from dataclasses import dataclass
from .executor_plan import build as build_entry
from .order_policy import choose
from .two_leg_runner import run
from .dynamic_exit import ExitState,decide
@dataclass
class ActiveTrade:
 id:str;symbol:str;plan:object;entry:object;exit_state:ExitState
class TradeOrchestrator:
 def __init__(self,long_executor,short_executor):
  self.long_executor=long_executor;self.short_executor=short_executor
 async def enter(self,symbol,long_venue,short_venue,base_amount,long_contract_size,short_contract_size,long_round,short_round,edge_pct,book_spread_pct,long_price,short_price):
  plan=build_entry(symbol,long_venue,short_venue,base_amount,long_contract_size,short_contract_size,long_round,short_round)
  if not plan.valid:raise RuntimeError(plan.reason)
  policy=choose(edge_pct,book_spread_pct,True)
  result=await run(plan,self.long_executor,self.short_executor,policy,long_price,short_price)
  if not result.hedged:raise RuntimeError("PARTIAL_HEDGE_REQUIRES_RECOVERY")
  return ActiveTrade(uuid.uuid4().hex,symbol,plan,result,ExitState(edge_pct,time.time()))
 def should_exit(self,trade,current_net,now=None,**kwargs):
  return decide(trade.exit_state,time.time() if now is None else now,current_net,**kwargs)
