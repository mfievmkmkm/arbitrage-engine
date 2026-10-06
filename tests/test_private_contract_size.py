import asyncio
from app.private_reader import PrivateReader
from app.account_health import probe
class C:
 async def fetch_positions(self):return [{"contracts":1,"symbol":"X","side":"long"}]
 async def fetch_open_orders(self):return []
 async def fetch_balance(self):return {"USDT":{"free":1,"used":0,"total":1}}
 def market(self,s):return {}
def test_unknown_contract_size_unhealthy():
 h,*_=asyncio.run(probe(PrivateReader("x",C())))
 assert not h.ok and h.error=="RuntimeError"
