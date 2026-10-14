from dataclasses import dataclass
@dataclass
class ExitState:
 entry_net_edge:float
 opened_at:float
 best_net:float=-1e18
@dataclass(frozen=True)
class ExitDecision:
 close:bool
 reason:str
def decide(state,now,current_net,target_capture=.70,trailing_drawdown=.20,max_seconds=1200,stop_net=None):
 state.best_net=max(state.best_net,current_net)
 target=max(0,state.entry_net_edge*target_capture)
 if current_net>=target:return ExitDecision(True,"TARGET_CAPTURE")
 if state.best_net>0 and current_net<=state.best_net*(1-trailing_drawdown):return ExitDecision(True,"NET_TRAILING")
 if stop_net is not None and current_net<=stop_net:return ExitDecision(True,"NET_STOP")
 if now-state.opened_at>=max_seconds:return ExitDecision(True,"TIME_STOP")
 return ExitDecision(False,"HOLD")
