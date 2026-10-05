def summarize(events):
 fills=[e for e in events if e.get("kind")=="FILL"]
 fees=sum(float(e.get("fee") or 0) for e in fills)
 venues=sorted({e.get("venue") for e in fills if e.get("venue")})
 states=[e.get("reason") for e in events if e.get("kind")=="STATE"]
 return {"events":len(events),"fills":len(fills),"fees":fees,"venues":venues,"states":states}
