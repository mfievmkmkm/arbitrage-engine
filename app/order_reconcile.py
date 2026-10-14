from dataclasses import dataclass
from .order_lifecycle import restart_action
@dataclass(frozen=True)
class ReconcileResult:
 safe:bool;actions:tuple
def plan(states):
 actions=[]
 for intent_id,state in states.items():
  a=restart_action(state)
  if a=="RECONCILE_EXCHANGE":actions.append((intent_id,a))
 return ReconcileResult(not actions,tuple(actions))
async def resolve(states,lookup):
 out=dict(states);unknown=[]
 for intent_id,state in states.items():
  if restart_action(state)!="RECONCILE_EXCHANGE":continue
  try:remote=await lookup(intent_id)
  except Exception:remote=None
  if remote in {"FILLED","CANCELED","REJECTED","FAILED"}:out[intent_id]=remote
  else:unknown.append(intent_id)
 return ReconcileResult(not unknown,tuple((x,"UNRESOLVED") for x in unknown)),out
