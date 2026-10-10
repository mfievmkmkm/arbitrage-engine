def rank(stats):
 rows=[]
 for venue,x in stats.items():
  score=float(x.get("net",0))*3+float(x.get("opportunities",0))*.1-float(x.get("incidents",0))*5-float(x.get("latency_ms",0))/500
  rows.append((venue,score))
 return sorted(rows,key=lambda x:x[1],reverse=True)
