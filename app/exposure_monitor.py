from dataclasses import dataclass
@dataclass(frozen=True)
class Exposure:
 gross_usd:float
 net_base:float
 hedged:bool
def calculate(base_long,base_short,long_price,short_price,tolerance_pct=.001):
 gross=abs(base_long*long_price)+abs(base_short*short_price)
 net=base_long-base_short
 scale=max(abs(base_long),abs(base_short),1e-12)
 return Exposure(gross,net,abs(net)<=scale*tolerance_pct)
