from dataclasses import dataclass
from .fill_journal import entry as journal_entry,exit as journal_exit

@dataclass(frozen=True)
class Completion:
 removed:bool
 reason:str

async def record_open(journal,trade_id,symbol,long_venue,short_venue,entry_result,recovery=None):
 await journal_entry(journal,trade_id,symbol,long_venue,short_venue,entry_result)
 if recovery is not None:
  await journal.event(trade_id,"ENTRY_RECOVERY",symbol=symbol,reason=recovery.action if hasattr(recovery,"action") else "RECOVERY")
 await journal.transition(trade_id,"HEDGED","PRIVATE_VERIFIED")

async def record_close(journal,trade,lifecycle):
 if lifecycle.execution is not None:await journal_exit(journal,trade,lifecycle.execution)
 if lifecycle.result is not None:
  r=lifecycle.result
  await journal.event(trade.trade_id,"TRADE_RESULT",symbol=trade.symbol,reason=r.reason,net=r.net)
 await journal.transition(trade.trade_id,"CLOSED",lifecycle.status)

def remove_verified(store,trades,trade_id,lifecycle):
 if not lifecycle.closed or lifecycle.status!="CLOSED_VERIFIED":return Completion(False,"NOT_VERIFIED_CLOSED")
 rows=[x for x in trades if x.trade_id!=trade_id]
 if len(rows)==len(trades):return Completion(False,"TRADE_NOT_FOUND")
 store.save(rows);return Completion(True,"REMOVED")
