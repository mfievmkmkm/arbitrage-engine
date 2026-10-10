from dataclasses import dataclass
@dataclass(frozen=True)
class LeverageGate:
 allowed:bool
 reason:str
def check(long_leverage,short_leverage,max_leverage=1):
 if long_leverage<=0 or short_leverage<=0:return LeverageGate(False,"LEVERAGE_UNKNOWN")
 if long_leverage>max_leverage or short_leverage>max_leverage:return LeverageGate(False,"LEVERAGE_TOO_HIGH")
 return LeverageGate(True,"OK")
