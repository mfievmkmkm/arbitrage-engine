from dataclasses import dataclass
from .state_invariants import check as invariant
from .crash_phase_policy import decide as phase_decide
@dataclass(frozen=True)
class RecoveryPlan:
 safe:bool
 action:str
 reason:str
def plan(phase,runtime_present,private_hedged,private_flat,unknown_orders):
 i=invariant(phase,runtime_present,private_hedged,private_flat,unknown_orders)
 if not i.safe:return RecoveryPlan(False,"HALT",i.reason)
 p=phase_decide(phase)
 if p.action=="HALT_UNKNOWN_PHASE":return RecoveryPlan(False,p.action,"UNKNOWN_PHASE")
 return RecoveryPlan(True,p.action,"OK")
