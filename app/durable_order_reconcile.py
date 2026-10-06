from .order_status import normalize

async def reconcile_and_persist(diary,executors,trade_id=None):
 states=await diary.order_intent_states(trade_id)
 intents=await diary.order_intents(trade_id)
 resolved=dict(states);unresolved=[]
 for iid,state in states.items():
  if state in {"FILLED","CANCELED","REJECTED","FAILED"}:continue
  meta=intents.get(iid) or {};ex=executors.get(meta.get("venue"))
  oid=meta.get("order_id");symbol=meta.get("symbol")
  if not ex or not symbol:unresolved.append(iid);continue
  result=None
  if oid:
   try:result=await ex.order(oid,symbol)
   except Exception:result=None
  if result is None and hasattr(ex,"order_by_client_id"):
   try:result=await ex.order_by_client_id(iid,symbol)
   except Exception:result=None
  if result is None:
   unresolved.append(iid);continue
  n=normalize(result.status,result.filled,meta.get("qty"))
  resolved[iid]=n
  await diary.update_order_intent_reconciled(iid,n,result)
  if n not in {"FILLED","CANCELED","REJECTED","FAILED"}:unresolved.append(iid)
 return resolved,tuple(unresolved)
