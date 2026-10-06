import asyncio
from .spot_future_symbols import normalize
from .spot_future_scanner import evaluate
class SpotFutureSource:
 def __init__(self,clients,notional,fee_pct=.2,safety_pct=.1,concurrency=6):self.clients=clients;self.notional=notional;self.fee_pct=fee_pct;self.safety_pct=safety_pct;self.sem=asyncio.Semaphore(concurrency);self.pairs={}
 async def load(self):
  for venue,c in self.clients.items():
   try:self.pairs[venue]=normalize(await c.load_markets())
   except Exception:self.pairs[venue]=[]
 async def _one(self,venue,c,pair,funding_pct=0):
  async with self.sem:
   try:
    s,f=await asyncio.gather(c.fetch_order_book(pair.spot_symbol),c.fetch_order_book(pair.future_symbol));return evaluate(venue,pair.base,pair.spot_symbol,pair.future_symbol,s,f,self.notional,self.fee_pct,funding_pct,self.safety_pct)
   except Exception:return None
 async def scan(self,limit_per_venue=20):
  jobs=[self._one(v,c,p) for v,c in self.clients.items() for p in self.pairs.get(v,[])[:limit_per_venue]];rows=await asyncio.gather(*jobs) if jobs else [];return sorted([x for x in rows if x],key=lambda x:x["hypothetical_edge"],reverse=True)
