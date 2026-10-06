from dataclasses import dataclass
@dataclass(frozen=True)
class CloseAcceptance:
 accepted:bool
 reason:str
def evaluate(lifecycle,runtime_present,private_flat):
 if not runtime_present:return CloseAcceptance(False,"RUNTIME_TRADE_MISSING")
 if not lifecycle.closed:return CloseAcceptance(False,"LIFECYCLE_NOT_CLOSED")
 if lifecycle.status!="CLOSED_VERIFIED":return CloseAcceptance(False,"CLOSE_NOT_VERIFIED")
 if not private_flat:return CloseAcceptance(False,"PRIVATE_NOT_FLAT")
 if lifecycle.result is None:return CloseAcceptance(False,"FINAL_PNL_MISSING")
 return CloseAcceptance(True,"ACCEPTED")
