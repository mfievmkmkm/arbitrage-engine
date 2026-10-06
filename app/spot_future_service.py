from .spot_future_source import SpotFutureSource
from .spot_future_candidate import check as candidate
from .strategy_observation import row
class Service:
 def __init__(self,clients,notional,min_edge,fee_pct=.2,safety_pct=.1):self.source=SpotFutureSource(clients,notional,fee_pct,safety_pct);self.min_edge=min_edge
 async def start(self):await self.source.load()
 async def cycle(self):
  rows=await self.source.scan();accepted=[]
  for x in rows:
   # Discovery keeps unknown funding visible, but Paper promotion requires evidence later.
   g=candidate(x,self.min_edge,True,True,True)
   if g.allowed:accepted.append(x)
  return accepted,[row(x) for x in rows]
