from dataclasses import dataclass
from .live_capacity import check as capacity
from .live_daily_risk import check as daily
from .withdraw_safety import verify as withdraw
from .venue_capability import evaluate as capability
from .live_status import build as status_build

@dataclass(frozen=True)
class CoordinatorDecision:
 allowed:bool
 reason:str
 status:object

class LiveCoordinator:
 def __init__(self,supervisor,bankroll,daily_stop_pct=2,max_open=1):
  self.supervisor=supervisor;self.bankroll=bankroll;self.daily_stop_pct=daily_stop_pct;self.max_open=max_open
 def admission(self,symbol,long_venue,short_venue,open_trades,realized_net,no_withdraw_attested,long_caps,short_caps):
  k=self.supervisor.kill.check(symbol,long_venue,short_venue)
  if k.blocked:return CoordinatorDecision(False,"KILL_"+k.scope+":"+k.reason,None)
  cap=capacity(open_trades,self.max_open);dr=daily(self.bankroll,realized_net,self.daily_stop_pct)
  wc=withdraw(no_withdraw_attested);lc=capability(**long_caps);sc=capability(**short_caps)
  st=status_build(self.supervisor,open_trades,cap.allowed,wc.safe,lc.live and sc.live)
  reasons=list(st.reasons)
  if not dr.allowed:reasons.append(dr.reason)
  reasons=tuple(dict.fromkeys(reasons))
  return CoordinatorDecision(not reasons,"OK" if not reasons else ",".join(reasons),st)
 def incident(self,reason):
  self.supervisor.failure(reason)
  return self.supervisor.kill.check("","","")
