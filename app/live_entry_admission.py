from dataclasses import dataclass
from .entry_cost import estimate
from .execution_pipeline import pre_submit

@dataclass(frozen=True)
class PreparedEntry:
 allowed:bool
 reason:str
 cost:object
 admission:object

def prepare(plan,policy,long_price,short_price,fee_schedule,min_net_edge_usd,live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,bankroll,daily_loss=0,books_fresh=True,risk_ok=True):
 try:
  cost=estimate(plan,policy,long_price,short_price,fee_schedule)
 except RuntimeError as e:
  return PreparedEntry(False,str(e),None,None)
 a=pre_submit(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,bankroll,plan.base_amount*((long_price+short_price)/2),daily_loss,books_fresh,risk_ok,cost.net_edge_usd,min_net_edge_usd)
 return PreparedEntry(a.allowed,"OK" if a.allowed else ",".join(a.reasons),cost,a)
