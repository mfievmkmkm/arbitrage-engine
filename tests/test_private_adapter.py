import asyncio,pytest
from app.private_adapter import DisabledAdapter
def test_disabled_adapter():
 async def run():
  a=DisabledAdapter();assert await a.positions()==[]
  with pytest.raises(RuntimeError):await a.flatten("BTC/USDT:USDT")
 asyncio.run(run())
