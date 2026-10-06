from dataclasses import dataclass
@dataclass(frozen=True)
class ResumeEvidence:
 safe:bool
 reasons:tuple
def collect(integrity,private_ready,unknown_orders,heartbeat,operator_stopped):
 reasons=[]
 if operator_stopped:reasons.append("OPERATOR_STOPPED")
 if not integrity.safe:reasons.append(integrity.reason)
 if not private_ready:reasons.append("PRIVATE_NOT_READY")
 if unknown_orders:reasons.append("UNKNOWN_ORDERS")
 if not heartbeat.healthy:reasons.append(heartbeat.reason)
 return ResumeEvidence(not reasons,tuple(dict.fromkeys(reasons)))
