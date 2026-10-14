import asyncio
from app.db import Diary
from app.live_order_intent import OrderIntent
def test_intent_roundtrip(tmp_path):
 async def go():
  d=Diary(str(tmp_path/"x.db"));await d.init();i=OrderIntent("i","t","x","BTC","buy",1,False)
  await d.save_order_intent(i,"SUBMITTED");assert (await d.order_intent_states())["i"]=="SUBMITTED"
 asyncio.run(go())
