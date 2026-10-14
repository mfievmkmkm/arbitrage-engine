from dataclasses import dataclass
@dataclass(frozen=True)
class ShutdownPlan:
 safe:bool
 action:str
def evaluate(open_trades,unknown_orders):
 if unknown_orders:return ShutdownPlan(False,"HALT_AND_RECONCILE")
 if open_trades:return ShutdownPlan(True,"PERSIST_AND_MONITOR_ON_RESTART")
 return ShutdownPlan(True,"CLEAN_SHUTDOWN")
