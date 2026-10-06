import asyncio
from .spot_spot_directional import best
class Source:
 def __init__(self,clients,notional,fees_pct=.2,safety_pct=.1):self.clients=clients;self.notional=notional;self.fees_pct=fees_pct;self.safety_pct=safety_pct
 async def scan_symbol(self,symbol):
  books={}
  async def get(v,c):
   try:books[v]=await c.fetch_order_book(symbol)
   except Exception:pass
  await asyncio.gather(*(get(v,c) for v,c in self.clients.items()));out=[]
  for a,ab in books.items():
   for b,bb in books.items():
    if a>=b:continue
    x=evaluate(symbol,a,b,ab,bb,self.notional,self.fees_pct,self.safety_pct)
    if x:out.append(x)
  return sorted(out,key=lambda x:x["net"],reverse=True)
