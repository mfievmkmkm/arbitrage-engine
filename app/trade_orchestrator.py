import time,uuid
from dataclasses import dataclass
from .executor_plan import build as build_entry
from .order_policy import choose
from .two_leg_runner import run
from .dynamic_exit import ExitState,decide
from .recovery_flow import recover
from .trade_lifecycle import Lifecycle,Phase\nfrom .fill_journal import entry as journal_entry
@dataclass
class ActiveTrade:
 id:str;symbol:str;plan:object;entry:object;exit_state:ExitState;lifecycle:object=None;recovery:object=None
class TradeOrchestrator:
 def __init__(self,long_executor,short_executor,journal=None):
  self.long_executor=long_executor;self.short_executor=short_executor;self.journal=journal
 async def enter(self,symbol,long_venue,short_venue,base_amount,long_contract_size,short_contract_size,long_round,short_round,edge_pct,book_spread_pct,long_price,short_price):
  trade_id=uuid.uuid4().hex;life=Lifecycle()
  plan=build_entry(symbol,long_venue,short_venue,base_amount,long_contract_size,short_contract_size,long_round,short_round)
  if not plan.valid:life.move(Phase.FAILED);raise RuntimeError(plan.reason)
  life.move(Phase.ENTERING)
  if self.journal:await self.journal.transition(trade_id,Phase.ENTERING)
  policy=choose(edge_pct,book_spread_pct,True)
  result=await run(plan,self.long_executor,self.short_executor,policy,long_price,short_price)\n  if self.journal:await journal_entry(self.journal,trade_id,symbol,long_venue,short_venue,result)
  recovery=None
  if not result.hedged:
   life.move(Phase.RECOVERY)
   if self.journal:await self.journal.transition(trade_id,Phase.RECOVERY,"PARTIAL_ENTRY")
   lb=result.long_result.filled*long_contract_size;sb=result.short_result.filled*short_contract_size
   recovery=await recover(symbol,long_venue,short_venue,lb,sb,long_contract_size,short_contract_size,self.long_executor,self.short_executor,edge_pct,0.0,max(edge_pct,0.0),long_round if lb<sb else short_round)
   if not recovery.completed:
    life.move(Phase.FAILED)
    if self.journal:await self.journal.transition(trade_id,Phase.FAILED,recovery.error or "RECOVERY_FAILED")
    raise RuntimeError("RECOVERY_FAILED")
  life.move(Phase.HEDGED)
  if self.journal:await self.journal.transition(trade_id,Phase.HEDGED)
  entry_edge_usd=abs(short_price-long_price)*plan.base_amount
  return ActiveTrade(trade_id,symbol,plan,result,ExitState(entry_edge_usd,time.time()),life,recovery)
 def should_exit(self,trade,current_net,now=None,**kwargs):
  return decide(trade.exit_state,time.time() if now is None else now,current_net,**kwargs)
