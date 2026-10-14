from dataclasses import dataclass
@dataclass(frozen=True)
class ScaleDecision:
 allowed:bool
 reason:str
def evaluate(current_notional,requested_notional,verified_trades,min_trades=20,max_step=1.25):
 if requested_notional<=current_notional:return ScaleDecision(True,"NO_SCALE_UP")
 if verified_trades<min_trades:return ScaleDecision(False,"INSUFFICIENT_VERIFIED_TRADES")
 if requested_notional>current_notional*max_step:return ScaleDecision(False,"SCALE_STEP_TOO_LARGE")
 return ScaleDecision(True,"CONTROLLED_SCALE")
