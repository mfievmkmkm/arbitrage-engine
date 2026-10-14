def metrics(rows,split=.7):
 n=len(rows);cut=max(1,min(n,int(n*split)));train=rows[:cut];test=rows[cut:]
 def calc(xs):
  vals=[float(x.get("net",x.get("pnl",0))) for x in xs];wins=sum(x for x in vals if x>0);loss=-sum(x for x in vals if x<0);peak=equity=dd=0
  for v in vals:equity+=v;peak=max(peak,equity);dd=max(dd,peak-equity)
  return {"n":len(vals),"net":sum(vals),"pf":wins/loss if loss else (999 if wins else 0),"max_dd":dd}
 return {"train":calc(train),"oos":calc(test)}
