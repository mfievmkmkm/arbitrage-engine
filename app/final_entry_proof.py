from dataclasses import dataclass
@dataclass(frozen=True)
class EntryProof:
 safe:bool
 reasons:tuple
def evaluate(arm,budget,leverage,position_mode,min_order,price_long,price_short,duplicate,execution_health):
 reasons=[]
 for x in (arm,budget,leverage,position_mode,min_order,price_long,price_short,duplicate,execution_health):
  ok=getattr(x,"valid",getattr(x,"allowed",getattr(x,"safe",False)))
  if not ok:reasons.append(getattr(x,"reason","UNSAFE"))
 return EntryProof(not reasons,tuple(dict.fromkeys(reasons)))
