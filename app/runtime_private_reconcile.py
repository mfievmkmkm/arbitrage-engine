from dataclasses import dataclass
@dataclass(frozen=True)
class RuntimeMatch:
 safe:bool
 reason:str
def verify_trade(t,snapshot,tolerance_pct=.001):
 def qty(venue,side):
  row=snapshot.get(venue)
  if not row or not getattr(row.get("health"),"ok",False):return None
  total=0.0;bad=0.0
  for p in row.get("positions",[]):
   if p.symbol!=t.symbol:continue
   q=abs(float(p.qty))
   if str(p.side).lower()==side:total+=q
   elif q>1e-12:bad+=q
  return None if bad>1e-12 else total
 l=qty(t.long_venue,"long");s=qty(t.short_venue,"short")
 if l is None or s is None:return RuntimeMatch(False,"PRIVATE_STATE_UNTRUSTED_OR_FLIPPED")
 scale=max(t.base_qty,l,s,1e-12)
 if abs(l-t.base_qty)>scale*tolerance_pct or abs(s-t.base_qty)>scale*tolerance_pct:return RuntimeMatch(False,"PERSISTED_PRIVATE_MISMATCH")
 return RuntimeMatch(True,"MATCHED")
def verify_all(trades,snapshot):
 for t in trades:
  x=verify_trade(t,snapshot)
  if not x.safe:return x
 return RuntimeMatch(True,"MATCHED")
