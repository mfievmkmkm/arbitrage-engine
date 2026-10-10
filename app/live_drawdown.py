from dataclasses import dataclass
@dataclass(frozen=True)
class DrawdownState:
 halted:bool
 drawdown:float
 reason:str
def check(peak_equity,current_equity,max_drawdown_pct=2):
 if peak_equity<=0:return DrawdownState(True,0,"EQUITY_UNKNOWN")
 dd=max(0,(peak_equity-current_equity)/peak_equity*100)
 return DrawdownState(dd>=max_drawdown_pct,dd,"LIVE_DRAWDOWN_STOP" if dd>=max_drawdown_pct else "OK")
