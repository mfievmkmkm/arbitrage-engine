from dataclasses import dataclass
@dataclass(frozen=True)
class RecoveryBudget:
 allowed:bool
 reason:str
 max_extra_loss:float
def check(bankroll,estimated_extra_loss,max_pct=.25):
 cap=max(0,bankroll)*max(0,max_pct)/100
 return RecoveryBudget(estimated_extra_loss<=cap,"OK" if estimated_extra_loss<=cap else "RECOVERY_LOSS_LIMIT",cap)
