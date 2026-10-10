from dataclasses import dataclass
@dataclass(frozen=True)
class FillState:
 long_base:float
 short_base:float
 mismatch_base:float
 mismatch_pct:float
 hedged:bool
def reconcile(long_filled,long_contract_size,short_filled,short_contract_size,tolerance_pct=.1):
 lb=long_filled*long_contract_size;sb=short_filled*short_contract_size
 scale=max(abs(lb),abs(sb));diff=abs(lb-sb)
 pct=0.0 if scale==0 else diff/scale*100
 return FillState(lb,sb,diff,pct,scale>0 and pct<=tolerance_pct)
