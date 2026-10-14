from dataclasses import dataclass
@dataclass(frozen=True)
class Evidence:
 allowed:bool;reasons:tuple

def evaluate(closed,pf,oos_net,max_dd,incidents,min_closed=100,min_pf=1.2):
 r=[]
 if closed<min_closed:r.append("SAMPLE")
 if pf<min_pf:r.append("PROFIT_FACTOR")
 if oos_net<=0:r.append("OOS_NET")
 if max_dd<0:r.append("INVALID_DRAWDOWN")
 if incidents:r.append("INCIDENTS")
 return Evidence(not r,tuple(r))
