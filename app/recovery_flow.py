import asyncio
from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .recovery_executor import plan
@dataclass(frozen=True)
class RecoveryResult:
 action:str;completed:bool;result:object=None;error:str=""
async def recover(symbol,long_venue,short_venue,long_base,short_base,long_contract_size,short_contract_size,long_executor,short_executor,remaining_edge,complete_cost,flatten_cost,round_qty=None,timeout=8):
 p=plan(long_venue,short_venue,long_base,short_base,remaining_edge,complete_cost,flatten_cost)
 if p.action=="HEDGED":return RecoveryResult("HEDGED",True)
 if p.venue==long_venue:executor=long_executor;cs=long_contract_size
 else:executor=short_executor;cs=short_contract_size
 qty=p.base_amount/cs
 if round_qty:qty=round_qty(qty)
 if qty<=0:return RecoveryResult(p.action,False,None,"ZERO_RECOVERY_QTY")
 req=SubmitRequest(symbol,p.side,qty,"market",None,p.action=="FLATTEN",False)
 try:r=await asyncio.wait_for(executor.submit(req),timeout)
 except Exception as e:return RecoveryResult(p.action,False,None,type(e).__name__)
 return RecoveryResult(p.action,r.filled>=qty-1e-12,r)
