from dataclasses import dataclass
@dataclass(frozen=True)
class Risk:
 allowed:bool;reasons:tuple

def check(net_edge,minimum,base_qty,max_base,private_gate):
 reasons=[]
 if net_edge<minimum:reasons.append("NET_EDGE")
 if base_qty<=0 or base_qty>max_base:reasons.append("SIZE")
 if not private_gate.allowed:reasons.extend(private_gate.reasons)
 return Risk(not reasons,tuple(dict.fromkeys(reasons)))
