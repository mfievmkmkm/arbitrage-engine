from dataclasses import dataclass
@dataclass(frozen=True)
class FundingArb:
 allowed:bool;long_venue:str;short_venue:str;carry_pct:float;reason:str

def evaluate(a,b,rate_a,rate_b,interval_a,interval_b,fees_pct,holding_hours=8):
 if interval_a is None or interval_b is None:return FundingArb(False,a,b,0,"INTERVAL_UNKNOWN")
 ca=float(rate_a)*holding_hours/float(interval_a);cb=float(rate_b)*holding_hours/float(interval_b)
 carry=abs(cb-ca)-fees_pct
 long,short=(a,b) if rate_a<=rate_b else (b,a)
 return FundingArb(carry>0,long,short,carry,"OK" if carry>0 else "NET_CARRY_NONPOSITIVE")
