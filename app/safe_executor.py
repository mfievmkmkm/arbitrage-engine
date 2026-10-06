from .exchange_executor import ExchangeExecutor
from .order_lifecycle import transition
class SafeExecutor(ExchangeExecutor):
 def __init__(self,venue,inner,diary,gate):
  self.venue=venue;self.inner=inner;self.diary=diary;self.gate=gate
 async def submit_intent(self,intent,request):
  states=await self.diary.order_intent_states(intent.trade_id)
  if intent.intent_id in states:return None,"DUPLICATE_OR_UNRESOLVED_INTENT"
  if not self.gate():return None,"LIVE_GATE_LOCKED"
  await self.diary.save_order_intent(intent,"SUBMITTING")
  try:
   result=await self.inner.submit(request)
  except Exception:
   await self.diary.save_order_intent(intent,"UNKNOWN")
   return None,"SUBMIT_UNKNOWN_RECONCILE"
  state="FILLED" if result.filled>=request.qty-1e-12 else ("PARTIAL" if result.filled>0 else "ACK")
  if hasattr(self.diary,"save_order_intent_result"):await self.diary.save_order_intent_result(intent,state,result)
  else:await self.diary.save_order_intent(intent,state)
  return result,state
 async def submit(self,request):raise RuntimeError("USE_SUBMIT_INTENT")
 async def cancel(self,order_id,symbol):return await self.inner.cancel(order_id,symbol)
 async def order(self,order_id,symbol):return await self.inner.order(order_id,symbol)
