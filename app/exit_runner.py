import asyncio
from dataclasses import dataclass
from .exit_plan import build
from .fill_reconcile import reconcile
from .exchange_executor import SubmitResult
@dataclass(frozen=True)
class ExitResult:
 long_result:object;short_result:object;flat:bool;mismatch_pct:float;long_error:str="";short_error:str=""
async def run(symbol,long_contracts,short_contracts,long_contract_size,short_contract_size,long_executor,short_executor,timeout=8):
 p=build(symbol,long_contracts,short_contracts)
 rows=await asyncio.gather(asyncio.wait_for(long_executor.submit(p.long_close),timeout),asyncio.wait_for(short_executor.submit(p.short_close),timeout),return_exceptions=True)
 def norm(x):
  if isinstance(x,Exception):return SubmitResult("",type(x).__name__,0,None,0),type(x).__name__
  return x,""
 a,ae=norm(rows[0]);b,be=norm(rows[1]);s=reconcile(a.filled,long_contract_size,b.filled,short_contract_size)
 flat=not ae and not be and a.filled>=long_contracts-1e-12 and b.filled>=short_contracts-1e-12
 return ExitResult(a,b,flat,s.mismatch_pct,ae,be)
