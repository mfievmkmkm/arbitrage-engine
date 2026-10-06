from dataclasses import dataclass
from .stage5_runtime import admit
@dataclass(frozen=True)
class PipelineDecision:
 allowed:bool;phase:str;reasons:tuple
def pre_submit(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,bankroll,notional,daily_loss,books_fresh=True,risk_ok=True):
 a=admit(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,bankroll,notional,daily_loss)
 reasons=list(a.reasons)
 if not books_fresh:reasons.append("MARKET_DATA_STALE")
 if not risk_ok:reasons.append("RISK_BLOCKED")
 reasons=tuple(dict.fromkeys(reasons))
 return PipelineDecision(not reasons,"READY_TO_PERSIST_INTENT" if not reasons else "BLOCKED",reasons)
