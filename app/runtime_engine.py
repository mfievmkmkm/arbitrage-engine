import time
from .runtime_state import RuntimeTrade
from .opportunity_gate import check as gate
class RuntimeEngine:
 def __init__(self,orchestrator,store,min_edge=1.0,bankroll=50,max_trades=2,max_utilization_pct=50):
  self.orchestrator=orchestrator;self.store=store;self.min_edge=min_edge;self.bankroll=bankroll;self.max_trades=max_trades;self.max_utilization_pct=max_utilization_pct;self.trades=store.load()
 @property
 def open_notional(self):return sum(t.base_qty*((t.long_entry+t.short_entry)/2) for t in self.trades)
 async def consider(self,op,spec_long,spec_short,long_round,short_round,long_price,short_price,risk_ok=True,books_fresh=True,private_required=False,private_ready=False):
  d=gate(op,self.min_edge,risk_ok,books_fresh,private_required,private_ready,self.bankroll,self.open_notional,len(self.trades),self.max_trades,self.max_utilization_pct)
  if not d.allowed:return None,d.reason
  base=op["notional"]/long_price
  t=await self.orchestrator.enter(op["symbol"],op["buy"],op["sell"],base,spec_long.contract_size,spec_short.contract_size,long_round,short_round,op["hypothetical_edge"],abs(op.get("raw",0)-op.get("executable",0)),long_price,short_price)
  r=RuntimeTrade(t.id,op["symbol"],op["buy"],op["sell"],t.plan.base_amount,t.plan.long.contracts,t.plan.short.contracts,spec_long.contract_size,spec_short.contract_size,long_price,short_price,time.time())
  self.trades.append(r);self.store.save(self.trades);return r,"OPENED"
 def remove(self,trade_id):
  self.trades=[x for x in self.trades if x.trade_id!=trade_id];self.store.save(self.trades)
