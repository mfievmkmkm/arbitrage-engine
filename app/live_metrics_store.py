import json,os,tempfile
from dataclasses import asdict
class LiveMetricsStore:
 def __init__(self,path):self.path=path
 def save(self,metrics):
  d=os.path.dirname(self.path) or ".";os.makedirs(d,exist_ok=True);fd,tmp=tempfile.mkstemp(dir=d,prefix=".metrics-",text=True)
  try:
   with os.fdopen(fd,"w") as f:json.dump(asdict(metrics),f,separators=(",",":"))
   os.replace(tmp,self.path)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
 def load(self,cls):
  if not os.path.exists(self.path):return cls()
  with open(self.path) as f:return cls(**json.load(f))
