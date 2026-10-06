from dataclasses import dataclass
@dataclass(frozen=True)
class StateInvariant:
 safe:bool
 reason:str
def check(phase,runtime_present,private_hedged,private_flat,unknown_orders):
 if unknown_orders:return StateInvariant(False,"UNKNOWN_ORDERS")
 if phase=="HEDGED" and (not runtime_present or not private_hedged):return StateInvariant(False,"HEDGED_STATE_INCONSISTENT")
 if phase=="CLOSED_VERIFIED" and (runtime_present or not private_flat):return StateInvariant(False,"CLOSED_STATE_INCONSISTENT")
 return StateInvariant(True,"OK")
