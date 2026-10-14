from dataclasses import dataclass
from .runtime_state import RuntimeTrade

@dataclass(frozen=True)
class CommitResult:
 committed:bool
 reason:str
 trade:object=None

def commit(store,existing,live_result,plan,symbol,long_venue,short_venue,opened_at):
 if not live_result.opened or live_result.actual is None:return CommitResult(False,"ENTRY_NOT_VERIFIED")
 if any(t.trade_id==live_result.trade_id for t in existing):return CommitResult(False,"DUPLICATE_RUNTIME_TRADE")
 a=live_result.actual
 t=RuntimeTrade(live_result.trade_id,symbol,long_venue,short_venue,a.base_qty,live_result.entry.long_result.filled,live_result.entry.short_result.filled,plan.long.contract_size,plan.short.contract_size,a.long_price,a.short_price,opened_at,entry_fees=a.long_fee+a.short_fee)
 rows=list(existing)+[t]
 store.save(rows)
 return CommitResult(True,"COMMITTED",t)
