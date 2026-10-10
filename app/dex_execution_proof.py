from dataclasses import dataclass
@dataclass(frozen=True)
class DexProof:
 allowed:bool;reasons:tuple

def evaluate(safety,route,token,network,net):
 reasons=[]
 for x in (safety,route,token,network):
  if not x.allowed:reasons.extend(x.reasons)
 if not net.allowed:reasons.append("NET_NOT_POSITIVE")
 return DexProof(not reasons,tuple(dict.fromkeys(reasons)))
