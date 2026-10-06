import asyncio
from app.paper import PaperEngine
class D:
 async def create_paper_position(self,p):return 1
 async def update_paper_position(self,p):pass
 async def close_paper_position(self,p,r):pass
 async def open_paper_positions(self):return []
def test_funding_changes_net():
 async def go():
  e=PaperEngine(D(),50)
  o={"symbol":"X","buy":"a","sell":"b","notional":5,"entry_buy":100,"entry_sell":110,"executable":10,"exit_buy":100,"exit_sell":110,"exit_spread":10,"fee_pct":0,"funding_pct":1}
  p=await e.open(o);await e.mark_and_exit([o]);assert p.current_net_usd==.05
 asyncio.run(go())
