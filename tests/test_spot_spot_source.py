import asyncio
from app.spot_spot_source import Source
class C:
 def __init__(self,p):self.p=p
 async def fetch_order_book(self,s):return {"asks":[[self.p,10]],"bids":[[self.p-.1,10]]}
def test_spot_spot_source_compares_venues():
 async def go():
  x=await Source({"a":C(100),"b":C(105)},10).scan_symbol("X/USDT");assert x and x[0]["net"]>4
 asyncio.run(go())
