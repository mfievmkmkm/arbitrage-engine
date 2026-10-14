from dataclasses import dataclass
@dataclass(frozen=True)
class ReconcileAction:
 action:str
 reason:str
def decide(runtime_trade,private_match,unknown_orders):
 if unknown_orders:return ReconcileAction("HALT","UNKNOWN_ORDERS")
 if not private_match.safe:return ReconcileAction("HALT",private_match.reason)
 if runtime_trade is None:return ReconcileAction("OBSERVE","NO_OPEN_TRADE")
 return ReconcileAction("MONITOR_OPEN_TRADE","MATCHED")
