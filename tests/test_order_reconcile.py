import asyncio
from app.order_reconcile import plan,resolve
def test_restart_blocks_unresolved():
 assert not plan({"i":"SUBMITTED"}).safe
 async def go():
  r,s=await resolve({"i":"SUBMITTED"},lambda i:_done())
  assert r.safe and s["i"]=="FILLED"
 async def _done():return "FILLED"
 asyncio.run(go())
