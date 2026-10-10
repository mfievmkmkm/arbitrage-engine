import asyncio
from app.live_trade_store import Store
def test_live_store_is_write_ahead_and_active(tmp_path):
 async def go():
  s=Store(str(tmp_path/"x.db"));await s.init();await s.phase("t","ENTRY_SUBMITTING",symbol="X",long_venue="a",short_venue="b",planned_long=1,planned_short=1);assert (await s.active())[0]["trade_id"]=="t";await s.phase("t","CLOSED_PRIVATE_VERIFIED");assert not await s.active()
 asyncio.run(go())
