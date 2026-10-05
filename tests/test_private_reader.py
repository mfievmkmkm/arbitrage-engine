import asyncio
from app.private_reader import PrivateReader
class C:
 async def fetch_positions(self):return [{"symbol":"BTC/USDT:USDT","side":"long","contracts":2,"entryPrice":100}]
 async def fetch_open_orders(self):return [{"symbol":"BTC/USDT:USDT","id":"1","side":"sell","amount":2,"filled":1,"status":"open"}]
def test_reader():
 async def run():
  r=PrivateReader("x",C());p=await r.positions();o=await r.orders()
  assert p[0].qty==2 and o[0].filled==1
 asyncio.run(run())
