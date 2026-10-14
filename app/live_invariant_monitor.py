def evaluate(durable_rows,runtime_rows,unknown_orders,private_trusted):
 issues=[];db={x["trade_id"] for x in durable_rows};rt={x.trade_id for x in runtime_rows}
 if unknown_orders:issues.append("UNKNOWN_ORDERS")
 if db and not private_trusted:issues.append("PRIVATE_UNTRUSTED")
 if rt-db:issues.append("RUNTIME_WITHOUT_DURABLE")
 if db-rt:issues.append("DURABLE_WITHOUT_RUNTIME")
 return {"healthy":not issues,"issues":tuple(issues)}
