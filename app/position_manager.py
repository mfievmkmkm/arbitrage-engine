import time
from dataclasses import dataclass
from .live_pnl import calculate
from .dynamic_exit import ExitState,decide
from .exit_cost import estimate

@dataclass
class PositionMark:
 trade_id:str;net:float;gross:float;fees:float;funding:float;safety_buffer:float;decision:object

class PositionManager:
 def __init__(self,target_capture=.7,trailing=.2,max_seconds=1200,long_exit_fee_rate=0,short_exit_fee_rate=0,safety_buffer=0):
  self.target_capture=target_capture;self.trailing=trailing;self.max_seconds=max_seconds;self.states={}
  self.long_exit_fee_rate=long_exit_fee_rate;self.short_exit_fee_rate=short_exit_fee_rate;self.safety_buffer=safety_buffer
 def mark(self,t,current_long,current_short,exit_fees=None,funding=None,now=None):
  now=time.time() if now is None else now
  f=t.funding if funding is None else funding
  cost=estimate(t.base_qty,current_long,current_short,self.long_exit_fee_rate,self.short_exit_fee_rate,f,self.safety_buffer)
  ef=cost.fees if exit_fees is None else exit_fees
  p=calculate(t.base_qty,t.long_entry,t.short_entry,current_long,current_short,t.entry_fees,ef,f,self.safety_buffer)
  state=self.states.setdefault(t.trade_id,ExitState(max(0,abs(t.short_entry-t.long_entry)*t.base_qty-t.entry_fees),t.opened_at))
  d=decide(state,now,p.net,self.target_capture,self.trailing,self.max_seconds)
  return PositionMark(t.trade_id,p.net,p.gross,p.fees,p.funding,p.safety_buffer,d)
