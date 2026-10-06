import json,os
class Store:
 def __init__(self,path):self.path=path
 def load(self):
  try:
   with open(self.path) as f:return json.load(f)
  except Exception:return {}
 def save(self,x):
  os.makedirs(os.path.dirname(self.path) or ".",exist_ok=True);tmp=self.path+".tmp"
  with open(tmp,"w") as f:json.dump(x,f)
  os.replace(tmp,self.path)
