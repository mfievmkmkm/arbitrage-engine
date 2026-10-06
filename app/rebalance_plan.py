def recommend(balances,targets,min_transfer=5):
 out=[]
 for venue,target in targets.items():
  delta=float(target)-float(balances.get(venue,0))
  if abs(delta)>=min_transfer:out.append({"venue":venue,"delta":delta,"action":"DEPOSIT_MANUALLY" if delta>0 else "WITHDRAW_MANUALLY"})
 return out
