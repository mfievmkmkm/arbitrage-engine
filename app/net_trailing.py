from dataclasses import dataclass
@dataclass(frozen=True)
class Drawdown:
 exit:bool
 drawdown:float
 floor:float
def check(current_net,best_net,trailing_fraction,min_floor=0):
 best=max(float(best_net),0);floor=max(float(min_floor),best*(1-max(0,min(1,float(trailing_fraction)))))
 dd=best-float(current_net)
 return Drawdown(best>0 and current_net<=floor,dd,floor)
