import asyncio
from .private_adapter import PrivatePosition,PrivateOrder
from .account_reader import read_usdt
class PrivateReader:
 def __init__(self,venue,client,timeout=8):self.venue=venue;self.client=client;self.timeout=timeout
 def _size(self,symbol):
  try:
   market=self.client.market(symbol) or {};raw=market.get("contractSize")
  except Exception as e:raise RuntimeError("CONTRACT_SIZE_LOOKUP_FAILED") from e
  if raw is None:raise RuntimeError("CONTRACT_SIZE_UNKNOWN")
  size=float(raw)
  if size<=0:raise RuntimeError("CONTRACT_SIZE_INVALID")
  return size
 async def positions(self):
  rows=await asyncio.wait_for(self.client.fetch_positions(),timeout=self.timeout);out=[]
  for row in rows:
   contracts=float(row.get("contracts") or 0);symbol=row.get("symbol","");size=self._size(symbol)
   if contracts>0:out.append(PrivatePosition(self.venue,symbol,row.get("side",""),contracts*size,row.get("entryPrice"),contracts,size))
  return out
 async def balance(self):return await read_usdt(self.venue,self.client,self.timeout)
 async def orders(self):
  rows=await asyncio.wait_for(self.client.fetch_open_orders(),timeout=self.timeout);out=[]
  for r in rows:
   symbol=r.get("symbol","");size=self._size(symbol);amount=float(r.get("amount") or 0);filled=float(r.get("filled") or 0)
   out.append(PrivateOrder(self.venue,symbol,str(r.get("id","")),r.get("side",""),amount*size,filled*size,r.get("status",""),amount,filled,size))
  return out
