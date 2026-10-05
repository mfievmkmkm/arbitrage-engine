import asyncio,time
from .funding import FundingSnapshot
from .funding_cache import FundingCache
class FundingService:
 def __init__(self,clients,ttl=120,timeout=6):
  self.clients=clients;self.cache=FundingCache(ttl);self.timeout=timeout;self.errors={}
 async def get(self,exchange,symbol):
  cached=self.cache.get(exchange,symbol)
  if cached:return FundingSnapshot(exchange,symbol,cached["rate"],cached["next_ts"],None)
  client=self.clients.get(exchange)
  if not client or not client.has.get("fetchFundingRate"):return FundingSnapshot(exchange,symbol,None,None,None)
  try:
   row=await asyncio.wait_for(client.fetch_funding_rate(symbol),self.timeout)
   rate=row.get("fundingRate");next_ts=row.get("fundingTimestamp") or row.get("nextFundingTimestamp")
   self.cache.put(exchange,symbol,rate,next_ts)
   return FundingSnapshot(exchange,symbol,rate,next_ts,None)
  except Exception as e:
   self.errors[(exchange,symbol)]=type(e).__name__
   return FundingSnapshot(exchange,symbol,None,None,None)
 async def pair_carry_pct(self,long_exchange,short_exchange,symbol):
  a,b=await asyncio.gather(self.get(long_exchange,symbol),self.get(short_exchange,symbol))
  if a.rate is None or b.rate is None:return 0.0,False
  return ((b.rate-a.rate)*100),True
