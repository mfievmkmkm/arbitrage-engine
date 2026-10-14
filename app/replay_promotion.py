from dataclasses import dataclass
@dataclass(frozen=True)
class ReplayPromotion:
 allowed:bool
 reason:str
def evaluate(trades,profit_factor,max_drawdown,validation_net,min_trades=100,min_pf=1.2):
 if trades<min_trades:return ReplayPromotion(False,"INSUFFICIENT_TRADES")
 if profit_factor<min_pf:return ReplayPromotion(False,"PROFIT_FACTOR_LOW")
 if validation_net<=0:return ReplayPromotion(False,"OUT_OF_SAMPLE_NOT_PROFITABLE")
 if max_drawdown<0:return ReplayPromotion(False,"DRAWDOWN_INVALID")
 return ReplayPromotion(True,"PROMOTION_CANDIDATE")
