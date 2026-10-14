from dataclasses import dataclass
@dataclass(frozen=True)
class PrivateGate:
 allowed:bool;reasons:tuple

def check(spot_balance_ok,future_margin_ok,future_position_mode_ok,borrow_verified,direction):
 reasons=[]
 if not spot_balance_ok:reasons.append("SPOT_BALANCE")
 if not future_margin_ok:reasons.append("FUTURE_MARGIN")
 if not future_position_mode_ok:reasons.append("POSITION_MODE")
 if direction=="LONG_FUTURE_SHORT_SPOT" and not borrow_verified:reasons.append("BORROW_UNVERIFIED")
 return PrivateGate(not reasons,tuple(reasons))
