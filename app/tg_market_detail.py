def render(strategy,x):
 symbol=x.get("symbol") or x.get("base","—");edge=x.get("hypothetical_edge",x.get("net",x.get("carry_pct",0)))
 out=[f"<b>{symbol}</b>",f"<code>{strategy}</code>","",f"NET / EDGE  <b>{float(edge):+.3f}%</b>"]
 for k,label in (("executable","Executable"),("funding_pct","Funding"),("fee_pct","Fees"),("safety_pct","Safety")):
  if k in x:out.append(f"{label:<12} <b>{float(x[k]):+.3f}%</b>")
 if x.get("buy"):out+=["",f"LONG   <b>{x['buy'].upper()}</b>",f"SHORT  <b>{x.get('sell','').upper()}</b>"]
 if x.get("exchange"):out+=["",f"Venue   <b>{x['exchange'].upper()}</b>",f"Direction <code>{x.get('direction','—')}</code>"]
 out+=["","<i>Displayed edge is research/paper evidence unless the strategy is explicitly LIVE-certified.</i>"];return "\n".join(out)
