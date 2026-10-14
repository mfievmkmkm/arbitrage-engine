from dataclasses import dataclass
@dataclass(frozen=True)
class PriceBand:
 safe:bool
 reason:str
def check(reference,price,max_deviation_pct=1):
 if reference<=0 or price<=0:return PriceBand(False,"PRICE_INVALID")
 d=abs(price-reference)/reference*100
 return PriceBand(d<=max_deviation_pct,"OK" if d<=max_deviation_pct else "PRICE_BAND")
