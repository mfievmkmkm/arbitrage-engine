from statistics import median
def metrics(nets):
 if not nets:return None
 wins=[x for x in nets if x>0];loss=[x for x in nets if x<0];pf=sum(wins)/abs(sum(loss)) if loss else (float("inf") if wins else 0)
 eq=peak=dd=0
 for n in nets:eq+=n;peak=max(peak,eq);dd=max(dd,peak-eq)
 return {"trades":len(nets),"net":sum(nets),"median":median(nets),"win_rate":len(wins)/len(nets)*100,"profit_factor":pf,"max_drawdown":dd}
def promote(nets,min_trades=100,min_pf=1.2):
 m=metrics(nets);return bool(m and m["trades"]>=min_trades and m["net"]>0 and m["profit_factor"]>=min_pf),m
