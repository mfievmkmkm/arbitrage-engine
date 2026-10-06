from dataclasses import dataclass
from .crash_recovery import decide as crash_decide
from .order_intent_reconciler import reconcile as reconcile_intents

@dataclass(frozen=True)
class RestartDecision:
 safe:bool
 action:str
 unresolved:tuple
 resolved:dict
 crash:object

async def reconcile(states,intents,executors,private_snapshot,known_trade_symbols=()):
 resolved,unresolved=await reconcile_intents(states,intents,executors)
 if unresolved:return RestartDecision(False,"BLOCK_UNKNOWN_ORDERS",unresolved,resolved,None)
 c=crash_decide(private_snapshot,known_trade_symbols)
 if not c.safe:return RestartDecision(False,c.action,(),resolved,c)
 return RestartDecision(True,"RESUME_OBSERVATION",(),resolved,c)
