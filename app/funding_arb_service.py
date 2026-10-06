from .funding_arb_economics import calculate
from .funding_units import to_pct
from .funding_arb_scanner import FundingArb
class Service:
 def __init__(self,funding_service,venues,fee_pct=.2):self.fs=funding_service;self.venues=venues;self.fee_pct=fee_pct
 async def scan(self,symbol):
  snaps={v:await self.fs.get(v,symbol) for v in self.venues};out=[];vs=list(snaps)
  for i,a in enumerate(vs):
   for b in vs[i+1:]:
    x,y=snaps[a],snaps[b]
    if x.rate is None or y.rate is None or not x.interval_hours or not y.interval_hours:continue
    ra,rb=to_pct(x.rate),to_pct(y.rate);long,short=(a,b) if ra<=rb else (b,a);lr,sr=(ra,rb) if ra<=rb else (rb,ra);li,si=(x.interval_hours,y.interval_hours) if ra<=rb else (y.interval_hours,x.interval_hours)
    e=calculate(lr,sr,8,li,si,self.fee_pct,0.05,0.02)
    if e.allowed:out.append(FundingArb(True,long,short,e.net_pct,"OK"))
  return sorted(out,key=lambda z:z.carry_pct,reverse=True)
