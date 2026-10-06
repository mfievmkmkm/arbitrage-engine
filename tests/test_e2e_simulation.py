import asyncio
from app.e2e_simulation import run
class A:contract_size=.001
class B:contract_size=.01
def test_e2e(tmp_path):
 async def go():
  op={"symbol":"X","buy":"a","sell":"b","notional":1,"hypothetical_edge":10,"funding_known":True,"raw":10,"executable":10,"entry_buy":100,"entry_sell":110,"exit_buy":104,"exit_sell":106}
  x=await run(str(tmp_path/"state"),op,A(),B())
  assert x["status"]=="CLOSED" and x["result"].net>0 and x["remaining"]==0
 asyncio.run(go())
