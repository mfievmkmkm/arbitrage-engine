from dataclasses import dataclass
from .restart_reconcile import reconcile as reconcile_restart
from .runtime_private_reconcile import verify_all

@dataclass(frozen=True)
class OpenTradeRecovery:
 safe:bool
 action:str
 trades:tuple
 restart:object

async def recover(store,diary,executors,private_snapshot):
 trades=tuple(store.load())
 states=await diary.order_intent_states()
 intents=await diary.order_intents()
 r=await reconcile_restart(states,intents,executors,private_snapshot,[t.symbol for t in trades])
 if not r.safe:return OpenTradeRecovery(False,r.action,trades,r)
 if trades:
  m=verify_all(trades,private_snapshot)
  if not m.safe:return OpenTradeRecovery(False,m.reason,trades,r)
 return OpenTradeRecovery(True,"RESUME_OPEN_TRADES" if trades else "RESUME_OBSERVATION",trades,r)
