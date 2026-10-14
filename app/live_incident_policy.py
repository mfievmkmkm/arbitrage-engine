from dataclasses import dataclass
@dataclass(frozen=True)
class IncidentPolicy:
 action:str
 halt:bool
def classify(reason):
 r=str(reason or "").upper()
 if any(x in r for x in ("UNKNOWN","UNTRUSTED","FLIPPED","MISMATCH")):return IncidentPolicy("RECONCILE_AND_HALT",True)
 if any(x in r for x in ("PARTIAL","HEDGE","RECOVERY")):return IncidentPolicy("PROTECTIVE_FLATTEN",True)
 if any(x in r for x in ("STALE","FUNDING","NET_EDGE","SLIPPAGE")):return IncidentPolicy("BLOCK_NEW_ENTRY",False)
 if any(x in r for x in ("TIMEOUT","API","NETWORK")):return IncidentPolicy("COUNT_ERROR",False)
 return IncidentPolicy("REVIEW",False)
