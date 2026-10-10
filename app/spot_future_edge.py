from dataclasses import dataclass
@dataclass(frozen=True)
class SpotFutureEdge:
 executable_pct:float;net_pct:float;direction:str

def calculate(spot_ask,spot_bid,future_ask,future_bid,fees_pct,funding_pct=0,safety_pct=0):
 a=(future_bid-spot_ask)/spot_ask*100-fees_pct+funding_pct-safety_pct
 b=(spot_bid-future_ask)/future_ask*100-fees_pct-funding_pct-safety_pct
 return SpotFutureEdge((future_bid-spot_ask)/spot_ask*100,a,"LONG_SPOT_SHORT_FUTURE") if a>=b else SpotFutureEdge((spot_bid-future_ask)/future_ask*100,b,"LONG_FUTURE_SHORT_SPOT")
