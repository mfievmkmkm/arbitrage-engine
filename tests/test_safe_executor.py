import asyncio
from app.safe_executor import SafeExecutor
from app.mock_executor import MockExecutor
from app.exchange_executor import SubmitRequest
from app.live_order_intent import OrderIntent
class D:
 def __init__(self):self.s={}
 async def order_intent_states(self,trade_id=None):return self.s.copy()
 async def save_order_intent(self,i,state=None):self.s[i.intent_id]=state
def test_gate_and_duplicate():
 async def go():
  d=D();i=OrderIntent("i","t","x","X","buy",1,False);r=SubmitRequest("X","buy",1)
  x=SafeExecutor("x",MockExecutor(),d,lambda:False);assert (await x.submit_intent(i,r))[1]=="LIVE_GATE_LOCKED"
  x=SafeExecutor("x",MockExecutor(),d,lambda:True);assert (await x.submit_intent(i,r))[1]=="FILLED"
  assert (await x.submit_intent(i,r))[1]=="DUPLICATE_OR_UNRESOLVED_INTENT"
 asyncio.run(go())
