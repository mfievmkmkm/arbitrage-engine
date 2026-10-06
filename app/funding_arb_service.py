from .funding_arb_economics import calculate\nfrom .funding_units import to_pct
class Service:
 def __init__(self,funding_service,venues,fee_pct=.2):self.fs=funding_service;self.venues=venues;self.fee_pct=fee_pct
 async def scan(self,symbol):
  snaps={v:await self.fs.get(v,symbol) for v in self.venues};out=[]
  vs=list(snaps)
  for i,a in enumerate(vs):
   for b in vs[i+1:]:
    x,y=snaps[a],snaps[b]
    if not x.known or not y.known:continue
    r=evaluate(a,b,x.rate_pct,y.rate_pct,x.interval_hours,y.interval_hours,self.fee_pct)
    if r.allowed:out.append(r)
  return sorted(out,key=lambda x:x.carry_pct,reverse=True)
