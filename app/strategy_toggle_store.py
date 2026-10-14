import json,os
class Store:
 def __init__(self,path):self.path=path
 def load(self,defaults):
  try:
   with open(self.path,"r",encoding="utf8") as f:return {**defaults,**json.load(f)}
  except Exception:return dict(defaults)
 def save(self,state):
  tmp=self.path+".tmp";os.makedirs(os.path.dirname(self.path) or ".",exist_ok=True)
  with open(tmp,"w",encoding="utf8") as f:json.dump(state,f)
  os.replace(tmp,self.path)
