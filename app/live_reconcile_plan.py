def plan(db_rows,runtime_trades):
 rt={x.trade_id:x for x in runtime_trades};out=[]
 for x in db_rows:
  if x["trade_id"] not in rt:out.append({"trade_id":x["trade_id"],"action":"PRIVATE_RECONCILE","reason":"DB_ACTIVE_RUNTIME_MISSING"})
 for k in rt:
  if not any(x["trade_id"]==k for x in db_rows):out.append({"trade_id":k,"action":"GLOBAL_HALT","reason":"RUNTIME_WITHOUT_DB"})
 return out
