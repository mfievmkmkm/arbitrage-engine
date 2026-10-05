import time
from dataclasses import dataclass
from .live_pnl import calculate
from .dynamic_exit import ExitState,decide
@dataclass
class PositionMark:
 trade_id:str;net:float;gross:float;fees:float;funding:float;decision:object
class PositionManager:
 def __init__(self,target_capture=.7,trailing=.2,max_seconds=1200):
  self.target_capture=target_capture;self.trailing=trailing;self.max_seconds=max_seconds;self.states={}
 def mark(self,t,current_long,current_short,exit_fees=0,funding=None,now=None):
  now=time.time() if now is None else now
  f=t.funding if funding is None else funding
  p=calculate(t.base_qty,t.long_entry,t.short_entry,current_long,current_short,t.entry_fees,exit_fees,f)
  state=self.states.setdefault(t.trade_id,ExitState(abs(t.short_entry-t.long_entry)*t.base_qty,t.opened_at))
  d=decide(state,now,p.net,self.target_capture,self.trailing,self.max_seconds)
  return PositionMark(t.trade_id,p.net,p.gross,p.fees,p.funding,d)
