from dataclasses import dataclass
@dataclass(frozen=True)
class LiveStatus:
 mode:str
 can_enter:bool
 reasons:tuple
 open_trades:int
def build(supervisor,open_trades,capacity_ok,withdraw_ok,venues_ok):
 reasons=list(supervisor.readiness().reasons)
 if not capacity_ok:reasons.append("CAPACITY")
 if not withdraw_ok:reasons.append("WITHDRAW_PERMISSION")
 if not venues_ok:reasons.append("VENUE_CAPABILITY")
 reasons=tuple(dict.fromkeys(reasons))
 return LiveStatus("MICRO_LIVE_READY" if not reasons else "LOCKED",not reasons,reasons,open_trades)
def render(x):
 return "⚡ LIVE CONTROL\nРежим: "+x.mode+"\nОткрытых сделок: "+str(x.open_trades)+"\nВход: "+("РАЗРЕШЁН" if x.can_enter else "ЗАБЛОКИРОВАН")+(("\nПричины: "+", ".join(x.reasons)) if x.reasons else "")
