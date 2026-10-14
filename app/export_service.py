import csv,json
from pathlib import Path
def write_csv(path,rows):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 if not rows:p.write_text("",encoding="utf8");return str(p)
 keys=sorted(set().union(*(r.keys() for r in rows)))
 with p.open("w",newline="",encoding="utf8") as f:
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
  for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()})
 return str(p)
