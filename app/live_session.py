from dataclasses import dataclass
from .live_entry_flow import execute as entry_execute
from .live_entry_commit import commit as commit_entry
from .live_trade_lifecycle import close as close_execute
from .close_acceptance import evaluate as accept_close
from .live_trade_commit import record_open,record_close,remove_verified

@dataclass(frozen=True)
class SessionResult:
 ok:bool
 phase:str
 reason:str
 trade:object=None
 result:object=None

async def open_trade(store,journal,existing,entry_args,commit_args):
 r=await entry_execute(**entry_args)
 if not r.opened:return SessionResult(False,"ENTRY_FAILED",r.reason)
 c=commit_entry(store,existing,r,**commit_args)
 if not c.committed:return SessionResult(False,"COMMIT_FAILED",c.reason)
 await record_open(journal,r.trade_id,commit_args["symbol"],commit_args["long_venue"],commit_args["short_venue"],r.entry,r.recovery)
 return SessionResult(True,"OPEN",r.reason,c.trade)

async def close_trade(store,journal,trades,trade,long_executor,short_executor,private_snapshot,reason="EXIT"):
 r=await close_execute(trade,long_executor,short_executor,private_snapshot,reason)
 a=accept_close(r,True,r.closed)
 if not a.accepted:return SessionResult(False,"CLOSE_FAILED",a.reason,trade,r)
 await record_close(journal,trade,r)
 rm=remove_verified(store,trades,trade.trade_id,r)
 if not rm.removed:return SessionResult(False,"REMOVE_FAILED",rm.reason,trade,r)
 return SessionResult(True,"CLOSED",r.status,None,r.result)
