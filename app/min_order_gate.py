from dataclasses import dataclass
@dataclass(frozen=True)
class MinOrder:
 allowed:bool
 reason:str
def check(notional,long_min_notional,short_min_notional):
 if notional<long_min_notional:return MinOrder(False,"LONG_MIN_NOTIONAL")
 if notional<short_min_notional:return MinOrder(False,"SHORT_MIN_NOTIONAL")
 return MinOrder(True,"OK")
