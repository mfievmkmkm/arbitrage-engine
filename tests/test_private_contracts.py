import asyncio
from app.private_reader import PrivateReader
class C:
 def market(self,s):return {"contractSize":.001}
 async def fetch_positions(self):return [{"symbol":"X","side":"long","contracts":10,"entryPrice":100}]
 async def fetch_open_orders(self):return [{"symbol":"X","id":"1","side":"sell","amount":5,"filled":2,"status":"open"}]
def test_private_base_exposure():
 async def go():
  r=PrivateReader("v",C());p=(await r.positions())[0];o=(await r.orders())[0]
  assert p.qty==.01 and p.contracts==10
  assert o.qty==.005 and o.filled==.002
 asyncio.run(go())
