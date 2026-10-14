from dataclasses import dataclass
@dataclass(frozen=True)
class Budget:
 bankroll:float;max_trade_loss:float;daily_loss:float;max_notional:float;max_open_trades:int
def build(bankroll,trade_loss_pct=.5,daily_loss_pct=2,max_notional=5):
 b=max(0,float(bankroll))
 return Budget(b,b*trade_loss_pct/100,b*daily_loss_pct/100,min(max_notional,b*.10),1)
def allowed(budget,notional,realized_daily_loss):
 if notional<=0 or notional>budget.max_notional:return False,"NOTIONAL_LIMIT"
 if realized_daily_loss>=budget.daily_loss:return False,"DAILY_LOSS_LIMIT"
 return True,"OK"
