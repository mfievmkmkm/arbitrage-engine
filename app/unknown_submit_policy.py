from dataclasses import dataclass
@dataclass(frozen=True)
class Decision:action:str;reason:str
def decide(order_lookup,private_trusted,exposure):
 if order_lookup=="FOUND":return Decision("RECONCILE_FILL","ORDER_FOUND")
 if not private_trusted:return Decision("GLOBAL_HALT","PRIVATE_UNTRUSTED")
 if exposure==0:return Decision("ABORT_NO_RETRY","FLAT_VERIFIED")
 return Decision("PROTECTIVE_RECONCILE","EXPOSURE_PRESENT")
