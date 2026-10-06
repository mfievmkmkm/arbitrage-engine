from collections import defaultdict
def aggregate(rows):
 out=defaultdict(lambda:{"observations":0,"positive":0,"edge_sum":0})
 for r in rows:
  s=r.get("strategy","unknown");x=out[s];x["observations"]+=1;e=float(r.get("edge") or 0);x["edge_sum"]+=e;x["positive"]+=int(e>0)
 for x in out.values():x["avg_edge"]=x["edge_sum"]/x["observations"] if x["observations"] else 0
 return dict(out)
