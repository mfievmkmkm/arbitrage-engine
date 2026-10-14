from dataclasses import dataclass
from .reconcile import reconcile
@dataclass(frozen=True)
class StartupDecision:
 safe:bool
 action:str
 details:dict
def evaluate_startup(positions,open_orders,expected=None):
 r=reconcile(positions,expected)
 if open_orders:return StartupDecision(False,"CANCEL_OR_REVIEW_ORDERS",{"orders":len(open_orders),"exposure":r.exposure})
 if not r.trusted:return StartupDecision(False,"RECONCILE_POSITIONS",{"reason":r.reason,"exposure":r.exposure})
 return StartupDecision(True,"READY",{"exposure":r.exposure})
