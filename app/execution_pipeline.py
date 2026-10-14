from dataclasses import dataclass
from .stage5_runtime import admit

@dataclass(frozen=True)
class PipelineDecision:
 allowed:bool;phase:str;reasons:tuple

def pre_submit(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,bankroll,notional,daily_loss,books_fresh=True,risk_ok=True,net_edge_usd=None,min_net_edge_usd=None):
 a=admit(live_enabled,release_gate,startup_safe,private_streams,withdrawals_disabled,bankroll,notional,daily_loss)
 reasons=list(a.reasons)
 if not books_fresh:reasons.append("MARKET_DATA_STALE")
 if not risk_ok:reasons.append("RISK_BLOCKED")
 if net_edge_usd is None and min_net_edge_usd is not None:reasons.append("NET_EDGE_UNKNOWN")
 if net_edge_usd is not None and min_net_edge_usd is not None and net_edge_usd<min_net_edge_usd:reasons.append("NET_EDGE_TOO_LOW")
 reasons=tuple(dict.fromkeys(reasons))
 return PipelineDecision(not reasons,"READY_TO_PERSIST_INTENT" if not reasons else "BLOCKED",reasons)
