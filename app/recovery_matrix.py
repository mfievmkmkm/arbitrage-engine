from dataclasses import dataclass
from .recovery_executor import plan
@dataclass(frozen=True)
class Case:
 name:str;long_base:float;short_base:float;remaining_edge:float;complete_cost:float;flatten_cost:float
def evaluate(case,long_venue="long",short_venue="short"):
 return plan(long_venue,short_venue,case.long_base,case.short_base,case.remaining_edge,case.complete_cost,case.flatten_cost)
def standard_cases():
 return [
  Case("balanced",1,1,1,.1,.2),
  Case("long_heavy_complete",1,.5,1,.1,.2),
  Case("long_heavy_flatten",1,.5,1,.3,.2),
  Case("short_heavy_complete",.5,1,1,.1,.2),
  Case("short_heavy_flatten",.5,1,1,.3,.2),
  Case("edge_gone_long",1,.5,0,.1,.2),
  Case("edge_gone_short",.5,1,-1,.1,.2)]
