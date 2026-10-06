class StrategyRuntime:
 def __init__(self):self.latest={};self.enabled={"futures_futures":True,"spot_futures":True,"cex_dex":False}
 def update(self,name,rows):self.latest[name]=rows
 def top(self,name,n=8):return self.latest.get(name,[])[:n]
 def counts(self):return {k:len(v) for k,v in self.latest.items()}
