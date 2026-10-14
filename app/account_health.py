import asyncio,time
from dataclasses import dataclass
@dataclass
class AccountHealth:
 venue:str
 ok:bool
 positions:int=0
 orders:int=0
 latency_ms:float=0.0
 error:str=""
async def probe(reader):
 started=time.perf_counter()
 try:
  positions,orders,balance=await asyncio.gather(reader.positions(),reader.orders(),reader.balance())
  return AccountHealth(reader.venue,True,len(positions),len(orders),(time.perf_counter()-started)*1000),positions,orders,balance
 except Exception as e:
  return AccountHealth(reader.venue,False,latency_ms=(time.perf_counter()-started)*1000,error=type(e).__name__),[],[],None
