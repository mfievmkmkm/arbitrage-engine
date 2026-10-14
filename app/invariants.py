from dataclasses import dataclass
@dataclass(frozen=True)
class InvariantResult:
 ok:bool;reason:str
def entry(plan,edge_pct,min_edge,private_ready,live_enabled):
 if not plan.valid:return InvariantResult(False,plan.reason)
 if plan.base_amount<=0:return InvariantResult(False,"ZERO_EXPOSURE")
 scale=max(plan.long.base_amount,plan.short.base_amount)
 if abs(plan.long.base_amount-plan.short.base_amount)/scale>.001:return InvariantResult(False,"UNBALANCED_ENTRY")
 if edge_pct<min_edge:return InvariantResult(False,"EDGE_TOO_LOW")
 if live_enabled and not private_ready:return InvariantResult(False,"PRIVATE_NOT_READY")
 return InvariantResult(True,"OK")
def closed(long_remaining,short_remaining,tolerance=1e-12):
 if abs(long_remaining)>tolerance or abs(short_remaining)>tolerance:return InvariantResult(False,"RESIDUAL_EXPOSURE")
 return InvariantResult(True,"FLAT")
