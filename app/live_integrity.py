from dataclasses import dataclass
from .runtime_private_reconcile import verify_all
@dataclass(frozen=True)
class Integrity:
 safe:bool
 reason:str
def check(trades,snapshot,intent_states):
 unresolved=[k for k,v in intent_states.items() if v not in {"FILLED","CANCELED","REJECTED","FAILED","PLANNED"}]
 if unresolved:return Integrity(False,"UNRESOLVED_ORDER_INTENTS")
 x=verify_all(trades,snapshot)
 if not x.safe:return Integrity(False,x.reason)
 return Integrity(True,"OK")
