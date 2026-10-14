import asyncio
from app.order_intent_reconciler import reconcile
from app.exchange_executor import SubmitResult
class E:
 async def order(self,*a):return SubmitResult("o","closed",1,100)
def test_reconcile():
 async def go():
  s,u=await reconcile({"i":"SUBMITTED"},{"i":{"venue":"x","order_id":"o","symbol":"X","qty":1}},{"x":E()})
  assert not u and s["i"]=="FILLED"
 asyncio.run(go())
