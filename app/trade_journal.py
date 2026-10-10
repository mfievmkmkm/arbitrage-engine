from .execution_diary import ExecutionEvent
class TradeJournal:
 def __init__(self,diary):self.diary=diary
 async def event(self,trade_id,kind,venue="",symbol="",side="",qty=0,price=None,fee=0,reason="",net=None,spread=None,phase=""):
  e=ExecutionEvent(trade_id,kind,venue,symbol,side,qty,price,fee,reason,0.0,net,spread,phase)
  await self.diary.record_execution_event(e)
 async def transition(self,trade_id,phase,reason=""):
  await self.event(trade_id,"STATE",reason=reason or str(phase),phase=str(phase))
 async def mark(self,trade_id,symbol,net,spread,price=None,reason=""):
  await self.event(trade_id,"MARK",symbol=symbol,price=price,reason=reason,net=net,spread=spread)
