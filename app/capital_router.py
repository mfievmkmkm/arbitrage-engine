from dataclasses import dataclass
@dataclass(frozen=True)
class Route:
 allowed:bool;venues:tuple;reason:str

def choose(scores,balances,min_balance):
 ranked=[v for v,_ in sorted(scores.items(),key=lambda x:x[1],reverse=True) if balances.get(v,0)>=min_balance]
 if len(ranked)<2:return Route(False,tuple(ranked),"INSUFFICIENT_FUNDED_VENUES")
 return Route(True,tuple(ranked[:2]),"OK")
