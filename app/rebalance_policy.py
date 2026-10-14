from dataclasses import dataclass
@dataclass(frozen=True)
class RebalancePolicy:
 automatic:bool;reason:str

def decide(needed,withdrawals_enabled=False):
 if not needed:return RebalancePolicy(False,"NOT_NEEDED")
 if not withdrawals_enabled:return RebalancePolicy(False,"MANUAL_REBALANCE_REQUIRED")
 return RebalancePolicy(False,"AUTO_WITHDRAWALS_FORBIDDEN")
