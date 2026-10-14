import asyncio,time
from .strategy_observation import row
class StrategyOrchestrator:
 def __init__(self,runtime,spot_future=None):self.runtime=runtime;self.spot_future=spot_future
 async def cycle(self,futures_rows):
  ts=time.time();self.runtime.update("futures_futures",futures_rows,ts);out={"futures_futures":futures_rows}
  if self.spot_future:
   try:rows=await self.spot_future.scan();self.runtime.update("spot_futures",rows,ts);out["spot_futures"]=rows
   except Exception:self.runtime.fail("spot_futures");out["spot_futures"]=[]
  return out
 def observations(self,result):return [row(x) for rows in result.values() for x in rows if x.get("strategy")]
