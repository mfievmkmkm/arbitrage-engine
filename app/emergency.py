from dataclasses import dataclass
@dataclass(frozen=True)
class FlattenInstruction:
 venue:str;symbol:str;side:str;qty:float;reduce_only:bool=True
def plan(venue,symbol,signed_qty):
 if abs(signed_qty)<=1e-12:return None
 side="sell" if signed_qty>0 else "buy"
 return FlattenInstruction(venue,symbol,side,abs(signed_qty),True)
def plans(exposures):
 return [x for x in (plan(v,s,q) for v,s,q in exposures) if x]
