from dataclasses import dataclass
@dataclass(frozen=True)
class OpportunityCost:
 should_exit:bool
 reason:str
def compare(current_expected_net,new_expected_net,switch_cost,min_improvement):
 gain=float(new_expected_net)-float(current_expected_net)-max(0,float(switch_cost))
 return OpportunityCost(gain>=min_improvement,"BETTER_OPPORTUNITY" if gain>=min_improvement else "KEEP_CURRENT")
