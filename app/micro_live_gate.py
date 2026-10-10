from dataclasses import dataclass
@dataclass(frozen=True)
class MicroLiveDecision:
 allowed:bool;reasons:tuple
def check(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,notional,max_notional=5):
 reasons=[]
 if not live_enabled:reasons.append("LIVE_DISABLED")
 if not release_gate.micro_live:reasons.append("RELEASE_GATE_LOCKED")
 if not startup_safe:reasons.append("STARTUP_UNSAFE")
 if not private_streams:reasons.append("PRIVATE_STREAMS_NOT_READY")
 if not withdrawals_disabled:reasons.append("WITHDRAWALS_PERMISSION_RISK")
 if notional<=0 or notional>max_notional:reasons.append("MICRO_NOTIONAL_INVALID")
 return MicroLiveDecision(not reasons,tuple(reasons))
