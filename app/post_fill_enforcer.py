from dataclasses import dataclass
from .post_fill_guard import check
from .persisted_exit_runner import run as emergency_exit

@dataclass(frozen=True)
class PostFillOutcome:
 keep:bool
 flattened:bool
 reason:str
 execution:object=None

async def enforce(actual,plan,trade,long_executor,short_executor,min_net_edge_usd,safety_buffer=0):
 d=check(actual,actual.base_qty,min_net_edge_usd,safety_buffer)
 if d.safe:return PostFillOutcome(True,False,d.reason)
 x=await emergency_exit(trade,long_executor,short_executor)
 if x.execution is None:return PostFillOutcome(False,False,"EMERGENCY_EXIT_UNCERTAIN:"+x.status)
 if not x.execution.flat:return PostFillOutcome(False,False,"EMERGENCY_EXIT_PARTIAL",x.execution)
 return PostFillOutcome(False,True,"ACTUAL_NET_DETERIORATED_FLATTENED",x.execution)
