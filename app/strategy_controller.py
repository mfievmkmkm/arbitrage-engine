class Controller:
 def __init__(self,store,defaults):self.store=store;self.state=store.load(defaults)
 def enabled(self,name):return bool(self.state.get(name,False))
 def toggle(self,name):self.state[name]=not self.enabled(name);self.store.save(self.state);return self.state[name]
