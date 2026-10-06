class CycleService:
 def __init__(self,service,symbols,batch=5):self.service=service;self.symbols=list(symbols);self.batch=batch;self.i=0
 async def cycle(self):
  if not self.symbols:return []
  xs=[self.symbols[(self.i+j)%len(self.symbols)] for j in range(min(self.batch,len(self.symbols)))];self.i=(self.i+len(xs))%len(self.symbols);out=[]
  for s in xs:
   for x in await self.service.scan(s):out.append({"strategy":"funding_arb","symbol":s,"long_venue":x.long_venue,"short_venue":x.short_venue,"carry_pct":x.carry_pct})
  return sorted(out,key=lambda x:x["carry_pct"],reverse=True)
