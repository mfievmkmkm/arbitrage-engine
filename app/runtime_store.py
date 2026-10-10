import json,os,tempfile
from .runtime_state import RuntimeTrade
class RuntimeStore:
 def __init__(self,path):self.path=path
 def save(self,trades):
  directory=os.path.dirname(self.path) or ".";os.makedirs(directory,exist_ok=True)
  fd,tmp=tempfile.mkstemp(dir=directory,prefix=".runtime-",text=True)
  try:
   with os.fdopen(fd,"w") as f:json.dump([x.row() for x in trades],f,separators=(",",":"))
   os.replace(tmp,self.path)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
 def load(self):
  if not os.path.exists(self.path):return []
  with open(self.path) as f:return [RuntimeTrade(**x) for x in json.load(f)]
