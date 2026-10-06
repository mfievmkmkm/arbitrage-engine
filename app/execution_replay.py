from collections import defaultdict
def build(events):
 groups=defaultdict(list)
 for e in events:groups[e["trade_id"]].append(e)
 out=[]
 for trade_id,rows in groups.items():
  rows=sorted(rows,key=lambda x:x.get("ts",0));net_marks=[]
  for e in rows:
   if e.get("kind")=="MARK" and "net" in e:net_marks.append((e["ts"],float(e["net"])))
  closes=[e for e in rows if e.get("kind")=="CLOSE"]
  out.append({"trade_id":trade_id,"events":len(rows),"marks":net_marks,"closed":bool(closes),"close_reason":closes[-1].get("reason","") if closes else ""})
 return out
def closed_net(dataset):
 vals=[]
 for x in dataset:
  if x["closed"] and x["marks"]:vals.append(x["marks"][-1][1])
 return vals
