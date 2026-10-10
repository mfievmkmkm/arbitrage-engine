import asyncio
from app.live_entry_flow import execute
from app.executor_plan import build
from app.fee_schedule import FeeSchedule,FeeRate
from app.mock_executor import MockExecutor
from app.private_adapter import PrivatePosition
class H:ok=True
def snap():return {"a":{"health":H(),"positions":[PrivatePosition("a","X","long",.04)]},"b":{"health":H(),"positions":[PrivatePosition("b","X","short",.04)]}}

class Safe:
 def __init__(self,inner):self.inner=inner;self.intents=[]
 async def submit_intent(self,intent,request):
  self.intents.append(intent)
  return await self.inner.submit(request),"FILLED"
 async def submit(self,request):return await self.inner.submit(request)

class G:micro_live=True

def kwargs():
 return dict(live_enabled=True,release_gate=G(),startup_safe=True,private_streams=True,withdrawals_disabled=True,bankroll=50,daily_loss=0,books_fresh=True,risk_ok=True)

def test_live_entry_persists_both_intents_and_opens_from_actual_fills():
 async def go():
  p=build("X","a","b",.04,.01,.01,lambda x:x,lambda x:x)
  a=Safe(MockExecutor(1,100));b=Safe(MockExecutor(1,110))
  r=await execute("X",p,a,b,100,110,10,.01,FeeSchedule({"a":FeeRate(0,0),"b":FeeRate(0,0)}),.1,kwargs(),private_snapshot=snap)
  assert r.opened and r.actual.base_qty==.04
  assert len(a.intents)==1 and len(b.intents)==1
 asyncio.run(go())

def test_live_entry_never_submits_when_net_gate_blocks():
 async def go():
  p=build("X","a","b",.04,.01,.01,lambda x:x,lambda x:x)
  a=Safe(MockExecutor());b=Safe(MockExecutor())
  r=await execute("X",p,a,b,100,100.1,.1,.01,FeeSchedule({"a":FeeRate(.01,.01),"b":FeeRate(.01,.01)}),.1,kwargs(),private_snapshot=snap)
  assert not r.opened and not a.intents and not b.intents
 asyncio.run(go())
