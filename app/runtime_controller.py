import time
from .position_manager import PositionManager
from .close_flow import close_trade
class RuntimeController:
 def __init__(self,runtime,long_executors=None,short_executors=None,position_manager=None,journal=None):
  self.runtime=runtime;self.long_executors=long_executors or {};self.short_executors=short_executors or {};self.pm=position_manager or PositionManager();self.journal=journal
 async def mark(self,trade,current_long,current_short,exit_fees=0,funding=None,now=None):
  m=self.pm.mark(trade,current_long,current_short,exit_fees,funding,now)
  if self.journal:await self.journal.event(trade.trade_id,"MARK",symbol=trade.symbol,price=current_long,reason=m.decision.reason)
  return m
 async def maybe_close(self,trade,current_long,current_short,exit_fees=0,funding=None,now=None):
  m=await self.mark(trade,current_long,current_short,exit_fees,funding,now)
  if not m.decision.close:return None,m,"HOLD"
  le=self.long_executors.get(trade.long_venue);se=self.short_executors.get(trade.short_venue)
  if not le or not se:return None,m,"EXECUTOR_UNAVAILABLE"
  r,x,status=await close_trade(trade,le,se,current_long,current_short,exit_fees,funding,m.decision.reason)
  if status=="CLOSED":
   self.runtime.remove(trade.trade_id)
   if self.journal:await self.journal.event(trade.trade_id,"CLOSE",symbol=trade.symbol,reason=m.decision.reason)
  return r,m,status
