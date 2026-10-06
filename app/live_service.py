from app.live_session import open_trade,close_trade
# Acceptance-oriented facade: callers must provide SafeExecutors, private snapshot
# and already validated market/risk evidence. It intentionally contains no API keys.
class LiveService:
 def __init__(self,store,journal):self.store=store;self.journal=journal
 async def open(self,existing,entry_args,commit_args):
  return await open_trade(self.store,self.journal,existing,entry_args,commit_args)
 async def close(self,trades,trade,long_executor,short_executor,private_snapshot,reason="EXIT"):
  return await close_trade(self.store,self.journal,trades,trade,long_executor,short_executor,private_snapshot,reason)
