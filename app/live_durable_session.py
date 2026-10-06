from .live_entry_safety_wrapper import before_submit,after_private_verified
from .live_close_safety_wrapper import before_close,after_private_flat
from .live_session import open_trade,close_trade
class Session:
 def __init__(self,durable_store,runtime_store,journal):self.durable=durable_store;self.runtime=runtime_store;self.journal=journal
 async def open(self,existing,entry_args,commit_args):
  tid=entry_args.get("trade_id_hint") or commit_args.get("trade_id_hint")
  if tid:await before_submit(self.durable,tid,symbol=commit_args["symbol"],long_venue=commit_args["long_venue"],short_venue=commit_args["short_venue"])
  r=await open_trade(self.runtime,self.journal,existing,entry_args,commit_args)
  if r.ok:await after_private_verified(self.durable,r.trade.trade_id,symbol=r.trade.symbol,long_venue=r.trade.long_venue,short_venue=r.trade.short_venue);await self.durable.phase(r.trade.trade_id,"OPEN",symbol=r.trade.symbol,long_venue=r.trade.long_venue,short_venue=r.trade.short_venue)
  return r
 async def close(self,trades,trade,long_executor,short_executor,private_snapshot,reason="EXIT"):
  await before_close(self.durable,trade,reason);r=await close_trade(self.runtime,self.journal,trades,trade,long_executor,short_executor,private_snapshot,reason)
  if r.ok:await after_private_flat(self.durable,trade,r.result)
  return r
