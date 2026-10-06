import asyncio,uuid
from dataclasses import dataclass
from .exchange_executor import SubmitRequest
from .order_policy import choose
from .live_order_intent import OrderIntent
from .fill_reconcile import reconcile
from .effective_entry import merge as merge_entry
from .recovery_flow import recover
from .two_leg_runner import TwoLegResult
from .actual_entry import build as actual_entry
from .live_entry_admission import prepare

@dataclass(frozen=True)
class LiveEntryResult:
 opened:bool
 reason:str
 trade_id:str
 entry:object=None
 actual:object=None
 recovery:object=None
 admission:object=None

async def execute(symbol,plan,long_executor,short_executor,long_price,short_price,edge_pct,book_spread_pct,fee_schedule,min_net_edge_usd,admission_kwargs,timeout=8,long_round=None,short_round=None):
 policy=choose(edge_pct,book_spread_pct,True)
 adm=prepare(plan,policy,long_price,short_price,fee_schedule,min_net_edge_usd,**admission_kwargs)
 if not adm.allowed:return LiveEntryResult(False,adm.reason,"",admission=adm)
 trade_id=uuid.uuid4().hex
 async def leg(name,leg,ex,price):
  req=SubmitRequest(symbol,leg.side,leg.contracts,policy.order_type,price,False,policy.ioc)
  intent=OrderIntent(trade_id+":"+name,trade_id,leg.venue,symbol,leg.side,leg.contracts,False)
  try:return await asyncio.wait_for(ex.submit_intent(intent,req),timeout)
  except Exception as e:return (None,"SUBMIT_"+type(e).__name__)
 l,s=await asyncio.gather(leg("entry-long",plan.long,long_executor,long_price),leg("entry-short",plan.short,short_executor,short_price))
 lr,ls=l;sr,ss=s
 if lr is None or sr is None:return LiveEntryResult(False,"ENTRY_UNCERTAIN:"+ls+":"+ss,trade_id,admission=adm)
 rec=reconcile(lr.filled,plan.long.contract_size,sr.filled,plan.short.contract_size)
 initial=TwoLegResult(lr,sr,rec.hedged,rec.mismatch_pct)
 recovery=None
 effective=initial
 if not initial.hedged:
  lb=lr.filled*plan.long.contract_size;sb=sr.filled*plan.short.contract_size
  recovery=await recover(symbol,plan.long.venue,plan.short.venue,lb,sb,plan.long.contract_size,plan.short.contract_size,long_executor,short_executor,edge_pct,0,max(edge_pct,0)+1e-12,long_round or (lambda x:x),timeout)
  if not recovery.completed:return LiveEntryResult(False,"ENTRY_RECOVERY_FAILED:"+recovery.error,trade_id,initial,None,recovery,adm)
  merged=merge_entry(plan,initial,recovery)
  if not merged.hedged:return LiveEntryResult(False,"ENTRY_RECOVERY_NOT_HEDGED:"+merged.reason,trade_id,merged.result,None,recovery,adm)
  effective=merged.result
 try:a=actual_entry(effective,plan)
 except RuntimeError as e:return LiveEntryResult(False,"ENTRY_ACTUAL_INVALID:"+str(e),trade_id,effective,None,recovery,adm)
 return LiveEntryResult(True,"OPENED",trade_id,effective,a,recovery,adm)
