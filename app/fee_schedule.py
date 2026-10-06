from dataclasses import dataclass

@dataclass(frozen=True)
class FeeRate:
 maker:float
 taker:float

class FeeSchedule:
 def __init__(self,rates=None):
  self.rates={str(k).lower():v for k,v in (rates or {}).items()}
 def rate(self,venue,liquidity="taker"):
  x=self.rates.get(str(venue).lower())
  if x is None:return None
  return x.maker if liquidity=="maker" else x.taker
 def require(self,venue,liquidity="taker"):
  r=self.rate(venue,liquidity)
  if r is None:raise RuntimeError("UNKNOWN_FEE_RATE:"+str(venue))
  return r

DEFAULT_FUTURES_FEES=FeeSchedule({
 "binance":FeeRate(.0002,.0005),
 "bybit":FeeRate(.0002,.00055),
 "okx":FeeRate(.0002,.0005),
 "mexc":FeeRate(.0001,.0004),
 "bitget":FeeRate(.0002,.0006),
 "gate":FeeRate(.0002,.0005),
})
