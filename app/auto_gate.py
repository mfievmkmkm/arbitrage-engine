from dataclasses import dataclass
@dataclass(frozen=True)
class AutoGate:
 allowed:bool;reasons:tuple

def evaluate(live_trades,profit_factor,max_drawdown_pct,incidents,unknown_orders,operator_approved,min_trades=50,min_pf=1.3,max_dd=2):
 reasons=[]
 if live_trades<min_trades:reasons.append("LIVE_SAMPLE")
 if profit_factor<min_pf:reasons.append("PROFIT_FACTOR")
 if max_drawdown_pct>max_dd:reasons.append("DRAWDOWN")
 if incidents:reasons.append("INCIDENTS")
 if unknown_orders:reasons.append("UNKNOWN_ORDERS")
 if not operator_approved:reasons.append("OPERATOR_APPROVAL")
 return AutoGate(not reasons,tuple(reasons))
