import time
from .tg_live_position import render
class Service:
 def __init__(self,store):self.store=store
 def cards(self,marks=None):
  marks=marks or {};out=[]
  for t in self.store.load():
   m=marks.get(t.trade_id,{});out.append((t,render(t,m.get("net_usd",0),m.get("spread",0),m.get("exposure",0),max(0,time.time()-m.get("opened_at",time.time())))))
  return out
