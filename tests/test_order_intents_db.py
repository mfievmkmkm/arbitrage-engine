import asyncio
from app.db import Diary
from app.live_order_intent import OrderIntent
def test_metadata(tmp_path):
 async def go():
  d=Diary(str(tmp_path/"x"));await d.init();await d.save_order_intent(OrderIntent("i","t","v","X","buy",2,False),"ACK")
  x=await d.order_intents();assert x["i"]["venue"]=="v" and x["i"]["qty"]==2
 asyncio.run(go())
