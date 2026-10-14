from dataclasses import dataclass
@dataclass(frozen=True)
class Limits:
 max_open:int=1;max_notional:float=5;max_leverage:float=1;daily_stop_pct:float=2

def validate(x):return x.max_open==1 and 0<x.max_notional<=5 and 0<x.max_leverage<=1 and 0<x.daily_stop_pct<=2
