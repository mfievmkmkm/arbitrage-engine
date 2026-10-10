from dataclasses import dataclass
@dataclass(frozen=True)
class DailyRisk:
 allowed:bool
 remaining_loss_usd:float
 reason:str
def check(bankroll,realized_net,daily_stop_pct=2):
 limit=max(0,float(bankroll))*max(0,float(daily_stop_pct))/100
 loss=max(0,-float(realized_net))
 remaining=max(0,limit-loss)
 return DailyRisk(loss<limit,remaining,"OK" if loss<limit else "DAILY_STOP")
