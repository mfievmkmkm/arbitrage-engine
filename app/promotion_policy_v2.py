def evaluate(m,incidents,min_oos=30,min_pf=1.2,max_dd=2):
 o=m["oos"];r=[]
 if o["n"]<min_oos:r.append("OOS_SAMPLE")
 if o["net"]<=0:r.append("OOS_NET")
 if o["pf"]<min_pf:r.append("OOS_PF")
 if o["max_dd"]>max_dd:r.append("MAX_DD")
 if incidents:r.append("INCIDENTS")
 return {"eligible":not r,"reasons":tuple(r)}
