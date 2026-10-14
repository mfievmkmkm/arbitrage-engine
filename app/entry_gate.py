from dataclasses import dataclass
@dataclass(frozen=True)
class GateResult:
 allowed:bool
 reason:str
def check(edge_pct,min_edge,risk_ok,books_fresh,funding_known,private_required=False,private_ready=False):
 if not risk_ok:return GateResult(False,"RISK_HALTED")
 if not books_fresh:return GateResult(False,"STALE_MARKET_DATA")
 if edge_pct<min_edge:return GateResult(False,"EDGE_TOO_LOW")
 if private_required and not private_ready:return GateResult(False,"PRIVATE_STATE_NOT_READY")
 if not funding_known:return GateResult(False,"FUNDING_UNKNOWN")
 return GateResult(True,"OK")
