from dataclasses import dataclass
@dataclass(frozen=True)
class Incident:severity:str;code:str;trade_id:str;action:str
def unknown_order(trade_id):return Incident("CRITICAL","UNKNOWN_ORDER",trade_id,"GLOBAL_HALT_RECONCILE")
def private_untrusted(trade_id):return Incident("CRITICAL","PRIVATE_UNTRUSTED",trade_id,"GLOBAL_HALT")
def hedge_mismatch(trade_id):return Incident("HIGH","HEDGE_MISMATCH",trade_id,"RECOVER_OR_FLATTEN")
