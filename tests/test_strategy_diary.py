import asyncio
from app.strategy_diary import init,record,summary
def test_strategy_observations_persist(tmp_path):
 async def go():
  p=str(tmp_path/"x.db");await init(p);await record(p,[{"ts":1,"strategy":"spot_futures","symbol":"X","venue":"a","edge":2,"notional":5,"payload":{}}]);x=await summary(p);assert x[0]["observations"]==1
 asyncio.run(go())
