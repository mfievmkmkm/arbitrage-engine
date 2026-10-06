from .venue_toggle_policy import set_mode
class Controller:
 def __init__(self,store,defaults):self.store=store;self.modes=store.load() or {x:{"scan":True,"paper":True,"real":False} for x in defaults}
 def get(self,name):return self.modes.setdefault(name,{"scan":True,"paper":True,"real":False})
 def toggle(self,name,mode,certified=False,acceptance=False):
  cur=self.get(name);new,ok,reason=set_mode(cur,mode,not cur.get(mode,False),certified,acceptance);self.modes[name]=new
  if ok:self.store.save(self.modes)
  return new,ok,reason
