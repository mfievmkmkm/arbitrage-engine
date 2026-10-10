from dataclasses import dataclass
@dataclass(frozen=True)
class ReplayMetrics:
 trades:int;net:float;win_rate:float;profit_factor:float;max_drawdown:float;median:float
def metrics(values):
 if not values:return ReplayMetrics(0,0,0,0,0,0)
 xs=list(values);wins=[x for x in xs if x>0];loss=[x for x in xs if x<0]
 equity=peak=dd=0
 for x in xs:
  equity+=x;peak=max(peak,equity);dd=max(dd,peak-equity)
 pf=sum(wins)/abs(sum(loss)) if loss else float("inf")
 s=sorted(xs);n=len(s);median=s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2
 return ReplayMetrics(n,sum(xs),len(wins)/n*100,pf,dd,median)
def compare(cases):
 return sorted(((name,metrics(vals)) for name,vals in cases.items()),key=lambda x:(x[1].net,-x[1].max_drawdown),reverse=True)
