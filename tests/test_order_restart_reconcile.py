import asyncio
from app.db import Diary
from app.safe_executor import SafeExecutor
from app.mock_executor import MockExecutor
from app.exchange_executor import SubmitRequest
from app.live_order_intent import OrderIntent
from app.order_intent_reconciler import reconcile

def test_persist_order_id_and_reconcile_after_restart(tmp_path):
 async def go():
  path=str(tmp_path/"d.db")
  d=Diary(path);await d.init()
  inner=MockExecutor(price=101)
  intent=OrderIntent("i","t","x","X","buy",1,False)
  result,state=await SafeExecutor("x",inner,d,lambda:True).submit_intent(intent,SubmitRequest("X","buy",1))
  assert state=="FILLED" and result.order_id
  rows=await d.order_intents("t")
  assert rows["i"]["order_id"]==result.order_id
  assert rows["i"]["filled"]==1
  d2=Diary(path)
  states=await d2.order_intent_states("t")
  rows2=await d2.order_intents("t")
  resolved,unresolved=await reconcile(states,rows2,{"x":inner})
  assert not unresolved and resolved["i"]=="FILLED"
  again=SafeExecutor("x",inner,d2,lambda:True)
  _,reason=await again.submit_intent(intent,SubmitRequest("X","buy",1))
  assert reason=="DUPLICATE_OR_UNRESOLVED_INTENT"
  assert inner.seq==1
 asyncio.run(go())
