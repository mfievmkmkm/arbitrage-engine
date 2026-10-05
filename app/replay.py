from dataclasses import dataclass
from statistics import median
@dataclass(frozen=True)
class ReplayParams: target:float;trailing:float;max_seconds:int
def simulate(entry,marks,p):
 best=0.;last=0.;reason="END"
 for ts,net,spread in marks:
  last=net;best=max(best,net);conv=1-spread/entry if entry>0 else 0
  if conv>=p.target and net>0:reason="TARGET";break
  if best>0 and net<=best*(1-p.trailing):reason="TRAILING";break
  if ts>=p.max_seconds:reason="TIME_STOP";break
 return {"net":last,"best":best,"reason":reason}
def parameter_sets():
 return [ReplayParams(a,b,c) for a in (.5,.6,.7,.8,.9) for b in (.1,.15,.2,.25) for c in (180,300,600,1200)]
def metrics(trades,p):
 nets=[simulate(e,m,p)["net"] for e,m in trades]
 if not nets:return None
 equity=peak=dd=0.;wins=[n for n in nets if n>0];losses=[n for n in nets if n<0]
 for n in nets:
  equity+=n;peak=max(peak,equity);dd=max(dd,peak-equity)
 pf=sum(wins)/abs(sum(losses)) if losses else (float("inf") if wins else 0.)
 return {"trades":len(nets),"net":sum(nets),"avg":sum(nets)/len(nets),"median":median(nets),"win_rate":len(wins)/len(nets)*100,"profit_factor":pf,"max_drawdown":dd}
def portfolio_replay(trades):
 rows=[]
 for p in parameter_sets():
  m=metrics(trades,p)
  if m:rows.append({"target":p.target,"trailing":p.trailing,"seconds":p.max_seconds,**m})
 return sorted(rows,key=lambda x:(x["net"],-x["max_drawdown"]),reverse=True)
def walk_forward(trades,train_ratio=.7,min_trades=10):
 if len(trades)<min_trades:return {"status":"insufficient_data","sample_size":len(trades)}
 cut=max(1,min(len(trades)-1,int(len(trades)*train_ratio)));train,test=trades[:cut],trades[cut:]
 ranked=portfolio_replay(train);best=ranked[0];p=ReplayParams(best["target"],best["trailing"],best["seconds"])
 return {"status":"validated_split","train":best,"test":metrics(test,p),"train_size":len(train),"test_size":len(test)}
def ai_report(rows):
 if not rows:return {"status":"insufficient_data","message":"Нет закрытых сделок с временным рядом."}
 b=rows[0];return {"status":"research_only","sample_size":b["trades"],"best_candidate":b,"warning":"Параметры не меняются автоматически."}
