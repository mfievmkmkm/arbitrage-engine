from .order_status import normalize
async def reconcile(states,intents,executors):
 resolved=dict(states);unresolved=[]
 for intent_id,state in states.items():
  if state in {"FILLED","CANCELED","REJECTED","FAILED","PLANNED"}:continue
  meta=intents.get(intent_id) or {};venue=meta.get("venue");oid=meta.get("order_id");symbol=meta.get("symbol")
  ex=executors.get(venue)
  if not ex or not oid or not symbol:unresolved.append(intent_id);continue
  try:r=await ex.order(oid,symbol)
  except Exception:unresolved.append(intent_id);continue
  n=normalize(r.status,r.filled,meta.get("qty"))
  resolved[intent_id]=n
  if n not in {"FILLED","CANCELED","REJECTED","FAILED"}:unresolved.append(intent_id)
 return resolved,tuple(unresolved)
