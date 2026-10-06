def from_rows(rows):
 edges=[float(x.get("edge") or 0) for x in rows if x.get("strategy")=="spot_futures"]
 return {"observations":len(edges),"positive":sum(x>0 for x in edges),"avg_edge":sum(edges)/len(edges) if edges else 0,"best_edge":max(edges) if edges else 0}
