import time
from dataclasses import dataclass,asdict
@dataclass
class PaperPosition:
 id:int;symbol:str;buy:str;sell:str;notional:float;entry_buy:float;entry_sell:float;entry_spread:float;opened_at:float;best_net_usd:float=0.;current_net_usd:float=0.;current_spread:float=0.;status:str="OPEN"
class PaperEngine:
 def __init__(self,diary,capital=50,max_positions=2,target=.70,trailing=.20,max_seconds=1200):
  self.diary=diary;self.capital=capital;self.max_positions=max_positions;self.target=target;self.trailing=trailing;self.max_seconds=max_seconds;self.positions={}
 async def restore(self):
  for row in await self.diary.open_paper_positions():
   p=PaperPosition(**row);self.positions[p.id]=p
 @property
 def used_capital(self):return sum(p.notional*2 for p in self.positions.values())
 def can_open(self,o):
  return len(self.positions)<self.max_positions and self.used_capital+o["notional"]*2<=self.capital and not any(p.symbol==o["symbol"] for p in self.positions.values())
 async def open(self,o):
  if not self.can_open(o):return None
  p=PaperPosition(0,o["symbol"],o["buy"],o["sell"],o["notional"],o["entry_buy"],o["entry_sell"],o["executable"],time.time(),current_spread=o["executable"])
  p.id=await self.diary.create_paper_position(asdict(p));self.positions[p.id]=p;return p
 async def mark_and_exit(self,ops):
  lookup={(o["symbol"],o["buy"],o["sell"]):o for o in ops};closed=[]
  for p in list(self.positions.values()):
   o=lookup.get((p.symbol,p.buy,p.sell))
   if not o:continue
   qty=p.notional/p.entry_buy
   gross=((o["exit_buy"]-p.entry_buy)+(p.entry_sell-o["exit_sell"]))*qty
   fees=p.notional*o["fee_pct"]/100
   funding=p.notional*float(o.get("funding_pct",0))/100
   p.current_net_usd=gross-fees+funding
   p.best_net_usd=max(p.best_net_usd,p.current_net_usd);p.current_spread=o["exit_spread"]
   await self.diary.update_paper_position(asdict(p))
   conv=1-p.current_spread/p.entry_spread if p.entry_spread>0 else 0;reason=None
   if conv>=self.target and p.current_net_usd>0:reason="TARGET"
   elif p.best_net_usd>0 and p.current_net_usd<=p.best_net_usd*(1-self.trailing):reason="TRAILING"
   elif time.time()-p.opened_at>=self.max_seconds:reason="TIME_STOP"
   if reason:closed.append(await self.close(p.id,reason))
  return closed
 async def close(self,pid,reason="MANUAL"):
  p=self.positions.get(pid)
  if not p:return None
  p.status="CLOSED";await self.diary.close_paper_position(asdict(p),reason);self.positions.pop(pid,None);return p
