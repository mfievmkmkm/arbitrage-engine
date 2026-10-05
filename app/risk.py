from dataclasses import dataclass
@dataclass
class RiskState:
    consecutive_errors:int=0
    paper_daily_pnl:float=0.0
    halted:bool=False
    reason:str=""
class RiskGuard:
    def __init__(self,bankroll=50.0,daily_stop_pct=2.0,max_errors=3):
        self.bankroll=bankroll;self.daily_stop_pct=daily_stop_pct;self.max_errors=max_errors;self.state=RiskState()
    def on_success(self): self.state.consecutive_errors=0
    def on_error(self):
        self.state.consecutive_errors+=1
        if self.state.consecutive_errors>=self.max_errors:self.halt("REPEATED_ERRORS")
    def on_paper_close(self,pnl):
        self.state.paper_daily_pnl+=pnl
        if self.state.paper_daily_pnl<=-(self.bankroll*self.daily_stop_pct/100):self.halt("PAPER_DAILY_STOP")
    def halt(self,reason):self.state.halted=True;self.state.reason=reason
    def can_open_paper(self):return not self.state.halted
