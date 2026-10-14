from dataclasses import dataclass
@dataclass(frozen=True)
class AuditEvent:
 kind:str
 severity:str
 message:str
 trade_id:str=""
def incident(reason,trade_id=""):
 r=str(reason)
 severity="CRITICAL" if any(x in r.upper() for x in ("UNKNOWN","UNTRUSTED","FLIPPED","MISMATCH","ERROR_CIRCUIT")) else "WARN"
 return AuditEvent("LIVE_INCIDENT",severity,r,trade_id)
