from .spot_spot_source import Source
class Service:
 def __init__(self,clients,symbols,notional,fees_pct=.2,safety_pct=.1,batch=5):self.source=Source(clients,notional,fees_pct,safety_pct);self.symbols=list(symbols);self.batch=batch;self.i=0
 async def cycle(self):
  if not self.symbols:return []
  xs=[self.symbols[(self.i+j)%len(self.symbols)] for j in range(min(self.batch,len(self.symbols)))];self.i=(self.i+len(xs))%len(self.symbols);out=[]
  for s in xs:out.extend(await self.source.scan_symbol(s))
  return sorted(out,key=lambda x:x["net"],reverse=True)[:50]
