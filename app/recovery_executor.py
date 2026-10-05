from dataclasses import dataclass
from .recovery import recovery_action
@dataclass(frozen=True)
class RecoveryPlan:
 action:str
 venue:str|None
 side:str|None
 base_amount:float
def plan(long_venue,short_venue,long_base,short_base,remaining_edge,complete_cost,flatten_cost,tolerance=1e-8):
 action=recovery_action(long_base,short_base,remaining_edge,complete_cost,flatten_cost,tolerance)
 if action=="HEDGED":return RecoveryPlan(action,None,None,0)
 if long_base>short_base:
  return RecoveryPlan(action,short_venue,"buy" if action=="COMPLETE" else "sell",long_base-short_base)
 return RecoveryPlan(action,long_venue,"buy" if action=="FLATTEN" else "buy",short_base-long_base)
