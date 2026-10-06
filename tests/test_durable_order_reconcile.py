import asyncio
from app.db import Diary
from app.live_order_intent import OrderIntent
from app.durable_order_reconcile import reconcile_and_persist
from app.exchange_executor import SubmitResult

class Ex:
 async def order_by_client_id(self,cid,symbol):return SubmitResult("exchange-1","closed",1,100,.01)

def test_unknown_order_recovers_by_client_id_and_persists(tmp_path):
 async def go():
  d=Diary(str(tmp_path/"d.db"));await d.init()
  i=OrderIntent("cid-1","t","a","X","buy",1,False)
  await d.save_order_intent(i,"UNKNOWN")
  r,u=await reconcile_and_persist(d,{"a":Ex()})
  assert not u and r["cid-1"]=="FILLED"
  assert (await d.order_intent_states())["cid-1"]=="FILLED"
  meta=(await d.order_intents())["cid-1"]
  assert meta["order_id"]=="exchange-1" and meta["filled"]==1
 asyncio.run(go())
