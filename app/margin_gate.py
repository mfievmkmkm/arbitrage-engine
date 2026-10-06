from dataclasses import dataclass
@dataclass(frozen=True)
class BalanceGate:
 allowed:bool
 reason:str
def check(required_margin,long_free,short_free,reserve_pct=20):
 need=required_margin*(1+max(0,reserve_pct)/100)
 if long_free<need:return BalanceGate(False,"LONG_MARGIN_LOW")
 if short_free<need:return BalanceGate(False,"SHORT_MARGIN_LOW")
 return BalanceGate(True,"OK")
