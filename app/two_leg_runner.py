import asyncio
from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .fill_reconcile import reconcile
@dataclass(frozen=True)
class TwoLegResult:
 long_result:object
 short_result:object
 hedged:bool
 mismatch_pct:float
async def run(plan,long_executor,short_executor,policy,long_price=None,short_price=None):
 if not plan.valid:raise RuntimeError(plan.reason)
 if policy.order_type=="none":raise RuntimeError(policy.reason)
 lr=SubmitRequest(plan.long.symbol,plan.long.side,plan.long.contracts,policy.order_type,long_price,False,policy.ioc)
 sr=SubmitRequest(plan.short.symbol,plan.short.side,plan.short.contracts,policy.order_type,short_price,False,policy.ioc)
 a,b=await asyncio.gather(long_executor.submit(lr),short_executor.submit(sr))
 state=reconcile(a.filled,plan.long.contract_size,b.filled,plan.short.contract_size)
 return TwoLegResult(a,b,state.hedged,state.mismatch_pct)
