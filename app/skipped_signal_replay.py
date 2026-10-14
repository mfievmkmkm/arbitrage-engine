def evaluate(signals,notional):
 out=[]
 for s in signals:
  edge=float(s.get("hypothetical_edge",s.get("net",0)));out.append({"ts":s.get("ts"),"symbol":s.get("symbol") or s.get("base"),"notional":notional,"estimated_net":notional*edge/100,"reason":s.get("skip_reason","SKIPPED")})
 return out
