from dataclasses import dataclass
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
def portfolio_replay(trades):
 rows=[]
 for p in parameter_sets():
  results=[simulate(e,m,p) for e,m in trades];nets=[r["net"] for r in results]
  if not nets:continue
  equity=0.;peak=0.;dd=0.
  for n in nets:
   equity+=n;peak=max(peak,equity);dd=max(dd,peak-equity)
  wins=sum(n>0 for n in nets)
  rows.append({"target":p.target,"trailing":p.trailing,"seconds":p.max_seconds,"trades":len(nets),"net":sum(nets),"win_rate":wins/len(nets)*100,"max_drawdown":dd})
 return sorted(rows,key=lambda x:(x["net"],-x["max_drawdown"]),reverse=True)
def ai_report(rows):
 if not rows:return {"status":"insufficient_data","message":"Нет закрытых сделок с временным рядом."}
 b=rows[0]
 return {"status":"research_only","sample_size":b["trades"],"best_candidate":b,"warning":"In-sample результат. Не менять торговые параметры автоматически; нужна out-of-sample проверка."}
