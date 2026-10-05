import asyncio
from dataclasses import dataclass
from .exit_plan import build
from .fill_reconcile import reconcile
@dataclass(frozen=True)
class ExitResult:
 long_result:object;short_result:object;flat:bool;mismatch_pct:float
async def run(symbol,long_contracts,short_contracts,long_contract_size,short_contract_size,long_executor,short_executor):
 p=build(symbol,long_contracts,short_contracts)
 a,b=await asyncio.gather(long_executor.submit(p.long_close),short_executor.submit(p.short_close))
 s=reconcile(a.filled,long_contract_size,b.filled,short_contract_size)
 expected_long=long_contracts*long_contract_size;expected_short=short_contracts*short_contract_size
 flat=a.filled>=long_contracts-1e-12 and b.filled>=short_contracts-1e-12
 return ExitResult(a,b,flat,s.mismatch_pct)
