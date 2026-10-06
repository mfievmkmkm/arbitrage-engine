import json,os
class Stop:
 def __init__(self,path):self.path=path;self.stopped=True;self.reason="RESTART_DEFAULT_STOP";self.load()
 def load(self):
  try:
   with open(self.path) as f:x=json.load(f);self.stopped=bool(x.get("stopped",True));self.reason=x.get("reason","")
  except Exception:pass
 def save(self):
  os.makedirs(os.path.dirname(self.path) or ".",exist_ok=True);tmp=self.path+".tmp"
  with open(tmp,"w") as f:json.dump({"stopped":self.stopped,"reason":self.reason},f)
  os.replace(tmp,self.path)
 def stop(self,reason="OPERATOR_STOP"):self.stopped=True;self.reason=reason;self.save()
 def resume(self,evidence):
  if not evidence.safe:return False
  self.stopped=False;self.reason="";self.save();return True
