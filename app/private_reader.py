import asyncio
from .private_adapter import PrivatePosition,PrivateOrder
class PrivateReader:
 def __init__(self,venue,client,timeout=8):
  self.venue=venue
  self.client=client
  self.timeout=timeout
 async def positions(self):
  rows=await asyncio.wait_for(self.client.fetch_positions(),timeout=self.timeout)
  out=[]
  for row in rows:
   qty=float(row.get("contracts") or 0)
   if qty>0:
    out.append(PrivatePosition(self.venue,row.get("symbol",""),row.get("side",""),qty,row.get("entryPrice")))
  return out
 async def orders(self):
  rows=await asyncio.wait_for(self.client.fetch_open_orders(),timeout=self.timeout)
  return [PrivateOrder(self.venue,r.get("symbol",""),str(r.get("id","")),r.get("side",""),float(r.get("amount") or 0),float(r.get("filled") or 0),r.get("status","")) for r in rows]
