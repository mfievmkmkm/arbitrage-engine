from dataclasses import dataclass
@dataclass(frozen=True)
class RebalanceNeed:
 needed:bool;from_venue:str;to_venue:str;amount:float

def calculate(balances,target):
 if not balances:return RebalanceNeed(False,"","",0)
 hi=max(balances,key=balances.get);lo=min(balances,key=balances.get);need=max(0,target-balances[lo]);extra=max(0,balances[hi]-target);amt=min(need,extra)
 return RebalanceNeed(amt>0,hi,lo,amt)
