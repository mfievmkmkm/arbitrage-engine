import asyncio
from .private_adapter import PrivateAdapter,PrivatePosition,PrivateOrder
class CCXTReadOnlyAdapter(PrivateAdapter):
 def __init__(self,venue,client,timeout=8):
  self.venue=venue;self.client=client;self.timeout=timeout
 async def positions(self):
  rows=await asyncio.wait_for(self.client.fetch_positions(),self.timeout)
  out=[]
  for x in rows:
   qty=float(x.get("contracts") or 0)
   if qty<=0:continue
   side=x.get("side") or ""
   out.append(PrivatePosition(self.venue,x.get("symbol",""),side,qty,x.get("entryPrice")))
  return out
 async def open_orders(self):
  rows=await asyncio.wait_for(self.client.fetch_open_orders(),self.timeout)
  return [PrivateOrder(self.venue,x.get("symbol",""),str(x.get("id","")),x.get("side",""),float(x.get("amount") or 0),float(x.get("filled") or 0),x.get("status","")) for x in rows]
 async def cancel(self,order_id,symbol):raise RuntimeError("READ_ONLY_PRIVATE_ADAPTER")
 async def flatten(self,symbol):raise RuntimeError("READ_ONLY_PRIVATE_ADAPTER")
