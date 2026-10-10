import asyncio
from app.live_bootstrap import bootstrap
def test_empty_bootstrap_is_not_live_ready():
 async def run():
  r=await bootstrap({})
  assert not r.configured and not r.ready
 asyncio.run(run())
