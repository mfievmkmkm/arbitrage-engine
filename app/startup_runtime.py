from dataclasses import dataclass
from .runtime_reconcile import reconcile as reconcile_runtime
from .crash_recovery import decide as crash_decide
@dataclass(frozen=True)
class StartupRuntime:
 safe:bool;mode:str;reason:str;details:dict
def evaluate(trades,snapshot,live_enabled=False,order_intents=None):
 if order_intents:
  from .order_reconcile import plan
  ir=plan(order_intents)
  if not ir.safe:return StartupRuntime(False,"OBSERVATION","UNRESOLVED_ORDER_INTENTS",{"intents":ir.actions})
 if not snapshot:
  if trades:return StartupRuntime(False,"OBSERVATION","RUNTIME_TRADES_WITHOUT_PRIVATE_STATE",{"trades":len(trades)})
  return StartupRuntime(True,"PAPER","NO_PRIVATE_VENUES",{})
 c=crash_decide(snapshot,[t.symbol for t in trades])
 if not c.safe:return StartupRuntime(False,"OBSERVATION",c.action,c.details)
 r=reconcile_runtime(trades,snapshot)
 if not r.trusted:return StartupRuntime(False,"OBSERVATION",r.reason,{"missing":r.missing,"unexpected":r.unexpected})
 if not live_enabled:return StartupRuntime(True,"PAPER","LIVE_DISABLED",{})
 return StartupRuntime(True,"LIVE_CANDIDATE","RECONCILED",{})
