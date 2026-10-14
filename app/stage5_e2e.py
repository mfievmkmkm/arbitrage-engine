from .micro_live_budget import build,allowed
from .live_order_intent import OrderIntent
from .safe_executor import SafeExecutor
from .exchange_executor import SubmitRequest
async def run(diary,inner):
 budget=build(50);ok,reason=allowed(budget,5,0)
 if not ok:return {"ok":False,"reason":reason}
 intent=OrderIntent("stage5-entry","stage5","mock","X","buy",1,False)
 locked=SafeExecutor("mock",inner,diary,lambda:False)
 _,locked_reason=await locked.submit_intent(intent,SubmitRequest("X","buy",1))
 if locked_reason!="LIVE_GATE_LOCKED":return {"ok":False,"reason":"GATE_FAILED"}
 armed=SafeExecutor("mock",inner,diary,lambda:True)
 result,state=await armed.submit_intent(intent,SubmitRequest("X","buy",1))
 return {"ok":state=="FILLED","state":state,"filled":0 if result is None else result.filled}
