import asyncio
from app.live_entry_flow import execute
from app.executor_plan import build
from app.fee_schedule import FeeSchedule,FeeRate
from app.mock_executor import MockExecutor
class Safe:
 def __init__(self,x):self.x=x;self.intents=[]
 async def submit_intent(self,i,r):self.intents.append(i);return await self.x.submit(r),"FILLED"
 async def submit(self,r):return await self.x.submit(r)
class G:micro_live=True
class H:ok=True
def test_private_entry_mismatch_triggers_reduce_only_protective_exit():
 async def go():
  p=build("X","a","b",.04,.01,.01,lambda x:x,lambda x:x);a=Safe(MockExecutor(1,100));b=Safe(MockExecutor(1,110))
  kw=dict(live_enabled=True,release_gate=G(),startup_safe=True,private_streams=True,withdrawals_disabled=True,bankroll=50,daily_loss=0,books_fresh=True,risk_ok=True)
  snap=lambda:{"a":{"health":H(),"positions":[]},"b":{"health":H(),"positions":[]}}
  r=await execute("X",p,a,b,100,110,10,.01,FeeSchedule({"a":FeeRate(0,0),"b":FeeRate(0,0)}),.1,kw,private_snapshot=snap,private_attempts=1,private_delay=0)
  assert not r.opened and "ENTRY_PRIVATE_UNVERIFIED" in r.reason
  assert len(a.intents)==2 and len(b.intents)==2
  assert a.intents[-1].reduce_only and b.intents[-1].reduce_only
 asyncio.run(go())
