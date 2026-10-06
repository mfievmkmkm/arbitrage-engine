from dataclasses import dataclass
from .micro_live_gate import check as micro_check
from .micro_live_budget import build as budget_build,allowed as budget_allowed
@dataclass(frozen=True)
class Admission:
 allowed:bool;reasons:tuple
def admit(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,bankroll,notional,daily_loss=0):
 reasons=[]
 m=micro_check(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,notional)
 if not m.allowed:reasons.extend(m.reasons)
 b=budget_build(bankroll);ok,reason=budget_allowed(b,notional,daily_loss)
 if not ok:reasons.append(reason)
 return Admission(not reasons,tuple(dict.fromkeys(reasons)))
