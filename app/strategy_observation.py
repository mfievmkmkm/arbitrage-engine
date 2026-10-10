def row(op):
 return {"ts":op["ts"],"strategy":op.get("strategy","unknown"),"symbol":op.get("base") or op.get("symbol"),"venue":op.get("exchange") or (op.get("buy","")+"->"+op.get("sell","")),"edge":op.get("hypothetical_edge"),"notional":op.get("notional"),"payload":op}
