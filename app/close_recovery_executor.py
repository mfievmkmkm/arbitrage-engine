import asyncio
from dataclasses import dataclass,replace
from .close_recovery import plan
from .exchange_executor import SubmitRequest
from .fill_reconcile import reconcile

@dataclass(frozen=True)
class RecoveryOutcome:
 execution:object
 recovered:bool
 reason:str
 recovery_result:object=None

async def recover_close(trade,exit_result,long_executor,short_executor,timeout=8,tolerance=1e-12):
 p=plan(trade,exit_result,tolerance)
 if not p.required:return RecoveryOutcome(exit_result,True,"FLAT_BY_FILLS")
 if not p.venue:return RecoveryOutcome(exit_result,False,p.reason)
 executor=long_executor if p.venue==trade.long_venue else short_executor
 req=SubmitRequest(trade.symbol,p.side,p.contracts,"market",None,True,False)
 try:r=await asyncio.wait_for(executor.submit(req),timeout)
 except Exception as e:return RecoveryOutcome(exit_result,False,"RECOVERY_"+type(e).__name__)
 if r.filled<p.contracts-tolerance:return RecoveryOutcome(exit_result,False,"RECOVERY_PARTIAL",r)
 if p.venue==trade.long_venue:
  lr=replace(exit_result.long_result,filled=exit_result.long_result.filled+r.filled,avg_price=r.avg_price if r.avg_price is not None else exit_result.long_result.avg_price,fee=exit_result.long_result.fee+r.fee)
  sr=exit_result.short_result
 else:
  lr=exit_result.long_result
  sr=replace(exit_result.short_result,filled=exit_result.short_result.filled+r.filled,avg_price=r.avg_price if r.avg_price is not None else exit_result.short_result.avg_price,fee=exit_result.short_result.fee+r.fee)
 rec=reconcile(lr.filled,trade.long_contract_size,sr.filled,trade.short_contract_size)
 flat=lr.filled>=trade.long_contracts-tolerance and sr.filled>=trade.short_contracts-tolerance
 merged=replace(exit_result,long_result=lr,short_result=sr,flat=flat,mismatch_pct=rec.mismatch_pct)
 return RecoveryOutcome(merged,flat,"RECOVERED" if flat else "RECOVERY_NOT_FLAT",r)
