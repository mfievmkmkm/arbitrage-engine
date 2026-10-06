import asyncio
from app.ledger_store import init,add,totals
def test_ledger_persists_attribution(tmp_path):
 async def go():
  p=str(tmp_path/"l.db");await init(p);await add(p,1,"funding",.2);await add(p,2,"fees",-.1);x=await totals(p);assert x["funding"]==.2 and x["fees"]==-.1
 asyncio.run(go())
