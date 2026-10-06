from dataclasses import dataclass
@dataclass(frozen=True)
class Residual:
 flat:bool;long_qty:float;short_qty:float;reason:str
def verify(snapshot,symbol,long_venue,short_venue,tolerance=1e-8):
 def qty(venue,side):
  row=snapshot.get(venue)
  if not row or not getattr(row.get("health"),"ok",False):return None
  total=0.0
  for p in row.get("positions",[]):
   if p.symbol==symbol and str(p.side).lower()==side:total+=abs(float(p.qty))
  return total
 l=qty(long_venue,"long");s=qty(short_venue,"short")
 if l is None or s is None:return Residual(False,l or 0,s or 0,"PRIVATE_STATE_UNTRUSTED")
 if l>tolerance or s>tolerance:return Residual(False,l,s,"RESIDUAL_EXPOSURE")
 return Residual(True,l,s,"FLAT")
