import asyncio,time
from .funding import FundingSnapshot
from .funding_cache import FundingCache
from .funding_timing import window,carry_pct
from .funding_interval import infer as infer_interval
class FundingService:
 def __init__(self,clients,ttl=120,timeout=6):
  self.clients=clients;self.cache=FundingCache(ttl);self.timeout=timeout;self.errors={}
 async def get(self,exchange,symbol):
  cached=self.cache.get(exchange,symbol)
  if cached:return FundingSnapshot(exchange,symbol,cached["rate"],cached["next_ts"],cached.get("interval_hours"))
  client=self.clients.get(exchange)
  if not client or not client.has.get("fetchFundingRate"):return FundingSnapshot(exchange,symbol,None,None,None)
  try:
   row=await asyncio.wait_for(client.fetch_funding_rate(symbol),self.timeout)
   rate=row.get("fundingRate");next_ts=row.get("fundingTimestamp") or row.get("nextFundingTimestamp")
   interval=infer_interval(row)
   self.cache.put(exchange,symbol,rate,next_ts,interval.hours if interval.known else None)
   return FundingSnapshot(exchange,symbol,rate,next_ts,interval.hours if interval.known else None)
  except Exception as e:
   self.errors[(exchange,symbol)]=type(e).__name__
   return FundingSnapshot(exchange,symbol,None,None,None)
 async def pair_carry_pct(self,long_exchange,short_exchange,symbol,hold_seconds=1200):
  a,b=await asyncio.gather(self.get(long_exchange,symbol),self.get(short_exchange,symbol))
  if a.rate is None or b.rate is None:return 0.0,False,"UNKNOWN"
  next_ts=min(x for x in (a.next_ts,b.next_ts) if x is not None) if a.next_ts is not None or b.next_ts is not None else None
  w=window(next_ts,hold_seconds,a.interval_hours or b.interval_hours)
  return carry_pct(a.rate,b.rate,w),True,w.reason
