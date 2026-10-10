import asyncio
from dataclasses import dataclass
from .exchange_executor import SubmitRequest,SubmitResult
from .fill_reconcile import reconcile
@dataclass(frozen=True)
class TwoLegResult:
 long_result:object;short_result:object;hedged:bool;mismatch_pct:float;long_error:str="";short_error:str=""
async def run(plan,long_executor,short_executor,policy,long_price=None,short_price=None,timeout=8):
 if not plan.valid:raise RuntimeError(plan.reason)
 if policy.order_type=="none":raise RuntimeError(policy.reason)
 lr=SubmitRequest(plan.long.symbol,plan.long.side,plan.long.contracts,policy.order_type,long_price,False,policy.ioc)
 sr=SubmitRequest(plan.short.symbol,plan.short.side,plan.short.contracts,policy.order_type,short_price,False,policy.ioc)
 rows=await asyncio.gather(asyncio.wait_for(long_executor.submit(lr),timeout),asyncio.wait_for(short_executor.submit(sr),timeout),return_exceptions=True)
 def norm(x):
  if isinstance(x,Exception):return SubmitResult("",type(x).__name__,0,None),type(x).__name__
  return x,""
 a,ae=norm(rows[0]);b,be=norm(rows[1])
 state=reconcile(a.filled,plan.long.contract_size,b.filled,plan.short.contract_size)
 return TwoLegResult(a,b,state.hedged,state.mismatch_pct,ae,be)
