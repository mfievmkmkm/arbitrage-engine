from dataclasses import dataclass
@dataclass(frozen=True)
class FundingSnapshot:
    exchange:str
    symbol:str
    rate:float|None
    next_ts:int|None
    interval_hours:float|None
def carry_pct(long_leg,short_leg):
    return ((short_leg.rate or 0.0)-(long_leg.rate or 0.0))*100
def adjusted_edge(edge_pct,long_leg,short_leg):
    return edge_pct+carry_pct(long_leg,short_leg)
