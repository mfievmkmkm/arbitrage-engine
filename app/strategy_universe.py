def common_spot_symbols(clients,min_venues=2,quote="USDT"):
 counts={}
 for c in clients.values():
  for m in c.markets.values():
   if m.get("spot") and m.get("quote")==quote and m.get("active") is not False:counts[m["symbol"]]=counts.get(m["symbol"],0)+1
 return sorted(s for s,n in counts.items() if n>=min_venues)
