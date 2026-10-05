from dataclasses import dataclass
@dataclass(frozen=True)
class ReplayParams:
 target:float; trailing:float; max_seconds:int
def simulate(entry_spread,marks,params):
 best=0.;last=0.;reason="END"
 for ts,net,spread in marks:
  last=net;best=max(best,net)
  conv=1-spread/entry_spread if entry_spread>0 else 0
  if conv>=params.target and net>0:reason="TARGET";break
  if best>0 and net<=best*(1-params.trailing):reason="TRAILING";break
  if ts>=params.max_seconds:reason="TIME_STOP";break
 return {"net":last,"best":best,"reason":reason}
def grid(entry_spread,marks):
 rows=[]
 for target in (.5,.6,.7,.8,.9):
  for trailing in (.1,.15,.2,.25):
   for seconds in (180,300,600,1200):
    p=ReplayParams(target,trailing,seconds);r=simulate(entry_spread,marks,p)
    rows.append({"target":target,"trailing":trailing,"seconds":seconds,**r})
 return sorted(rows,key=lambda x:x["net"],reverse=True)
