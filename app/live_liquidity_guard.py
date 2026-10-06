from dataclasses import dataclass
@dataclass(frozen=True)
class LiquidityState:
 safe:bool
 reason:str
def check(required_base,long_available,short_available,reserve_ratio=.1):
 need=required_base*(1+max(0,reserve_ratio))
 if long_available<need:return LiquidityState(False,"LONG_LIQUIDITY_THIN")
 if short_available<need:return LiquidityState(False,"SHORT_LIQUIDITY_THIN")
 return LiquidityState(True,"OK")
