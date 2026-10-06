from dataclasses import dataclass
@dataclass(frozen=True)
class LiveBudget:
 allowed:bool
 notional:float
 reason:str
def allocate(bankroll,requested,max_notional_pct=10,absolute_cap=5):
 cap=min(max(0,bankroll)*max_notional_pct/100,absolute_cap)
 if requested<=0:return LiveBudget(False,0,"INVALID_NOTIONAL")
 if requested>cap:return LiveBudget(False,cap,"MICRO_LIVE_NOTIONAL_LIMIT")
 return LiveBudget(True,requested,"OK")
