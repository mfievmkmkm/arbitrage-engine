from dataclasses import dataclass
from .net_trailing import check as trailing
from .live_time_stop import check as time_stop
from .opportunity_cost import compare
@dataclass(frozen=True)
class ExitSignal:
 exit:bool
 reason:str
def decide(current_net,best_net,trailing_fraction,opened_at,now,max_seconds,current_expected_net=0,new_expected_net=0,switch_cost=0,min_improvement=1e99):
 t=trailing(current_net,best_net,trailing_fraction)
 if t.exit:return ExitSignal(True,"NET_TRAILING")
 ts=time_stop(opened_at,now,max_seconds)
 if ts.exit:return ExitSignal(True,ts.reason)
 o=compare(current_expected_net,new_expected_net,switch_cost,min_improvement)
 if o.should_exit:return ExitSignal(True,o.reason)
 return ExitSignal(False,"HOLD")
