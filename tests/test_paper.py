import asyncio
from app.paper import PaperEngine
class FakeDiary:
 async def open_paper_positions(self):return []
 async def create_paper_position(self,p):return 1
 async def update_paper_position(self,p):pass
 async def close_paper_position(self,p,reason):pass
def opportunity():
 return dict(symbol="ABC/USDT:USDT",buy="binance",sell="bybit",notional=5,entry_buy=1.0,entry_sell=1.1,executable=10.0)
def test_target_exit():
 async def run():
  e=PaperEngine(FakeDiary(),capital=50,target=.5)
  p=await e.open(opportunity())
  closed=await e.mark_and_exit([dict(opportunity(),exit_buy=1.04,exit_sell=1.06,exit_spread=1.923,fee_pct=.21)])
  assert p.current_net_usd>0 and closed and closed[0].status=="CLOSED"
 asyncio.run(run())
def test_duplicate_symbol_and_capital():
 async def run():
  e=PaperEngine(FakeDiary(),capital=11,max_positions=2)
  o=opportunity();assert await e.open(o)
  assert not e.can_open(dict(o,buy="okx",sell="mexc"))
  other=dict(o,symbol="XYZ/USDT:USDT")
  assert not e.can_open(other)
 asyncio.run(run())
