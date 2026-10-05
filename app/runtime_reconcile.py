from dataclasses import dataclass
@dataclass(frozen=True)
class RuntimeReconcile:
 trusted:bool;reason:str;missing:list;unexpected:list
def reconcile(trades,snapshot,tolerance=.001):
 expected={}
 for t in trades:
  expected[(t.long_venue,t.symbol)]=expected.get((t.long_venue,t.symbol),0)+t.base_qty
  expected[(t.short_venue,t.symbol)]=expected.get((t.short_venue,t.symbol),0)-t.base_qty
 actual={}
 for venue,data in snapshot.items():
  for p in data["positions"]:
   q=p.qty if p.side.lower() in ("long","buy") else -p.qty
   actual[(venue,p.symbol)]=actual.get((venue,p.symbol),0)+q
 missing=[];unexpected=[]
 for k in set(expected)|set(actual):
  e=expected.get(k,0);a=actual.get(k,0);scale=max(abs(e),abs(a),1e-12)
  if abs(e-a)/scale>tolerance:
   (missing if abs(a)<abs(e) else unexpected).append((k,e,a))
 ok=not missing and not unexpected
 return RuntimeReconcile(ok,"MATCH" if ok else "EXPOSURE_MISMATCH",missing,unexpected)
