import csv,json
from io import StringIO
def csv_text(rows):
 if not rows:return ""
 keys=sorted(set().union(*(r.keys() for r in rows)));s=StringIO();w=csv.DictWriter(s,fieldnames=keys);w.writeheader()
 for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple)) else v for k,v in r.items()})
 return s.getvalue()
