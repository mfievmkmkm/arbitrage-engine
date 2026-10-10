from dataclasses import dataclass
@dataclass(frozen=True)
class LimitDecision:
 allowed:bool;reason:str
def check(bankroll,open_notional,new_notional,open_trades,max_trades=2,max_utilization_pct=50):
 if bankroll<=0:return LimitDecision(False,"NO_CAPITAL")
 if open_trades>=max_trades:return LimitDecision(False,"MAX_TRADES")
 if (open_notional+new_notional)/bankroll*100>max_utilization_pct:return LimitDecision(False,"MAX_UTILIZATION")
 return LimitDecision(True,"OK")
