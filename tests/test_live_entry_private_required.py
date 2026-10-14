import asyncio
from app.live_entry_flow import execute
from app.executor_plan import build
from app.fee_schedule import FeeSchedule,FeeRate
from app.mock_executor import MockExecutor
class S:
 def __init__(self,x):self.x=x;self.intents=[]
 async def submit_intent(self,i,r):self.intents.append(i);return await self.x.submit(r),"FILLED"
 async def submit(self,r):return await self.x.submit(r)
class G:micro_live=True
def test_private_snapshot_is_mandatory_after_real_fills():
 async def go():
  p=build("X","a","b",.04,.01,.01,lambda x:x,lambda x:x);a=S(MockExecutor(1,100));b=S(MockExecutor(1,110));kw=dict(live_enabled=True,release_gate=G(),startup_safe=True,private_streams=True,withdrawals_disabled=True,bankroll=50,daily_loss=0,books_fresh=True,risk_ok=True)
  x=await execute("X",p,a,b,100,110,10,.01,FeeSchedule({"a":FeeRate(0,0),"b":FeeRate(0,0)}),.1,kw);assert not x.opened and "PRIVATE_STATE_REQUIRED" in x.reason
 asyncio.run(go())
