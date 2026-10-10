import asyncio
from app.restart_reconcile import reconcile
class H:ok=True
def test_restart_blocks_unknown_intent_without_order_id():
 async def go():
  x=await reconcile({"i":"UNKNOWN"},{"i":{"venue":"a","symbol":"X"}},{},{"a":{"health":H(),"positions":[],"orders":[]}},())
  assert not x.safe and x.action=="BLOCK_UNKNOWN_ORDERS" and x.unresolved==("i",)
 asyncio.run(go())
def test_restart_resumes_only_when_orders_and_private_state_clean():
 async def go():
  x=await reconcile({}, {}, {}, {"a":{"health":H(),"positions":[],"orders":[]}},())
  assert x.safe and x.action=="RESUME_OBSERVATION"
 asyncio.run(go())
