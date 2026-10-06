from .strategy_runtime import StrategyRuntime
class UnifiedRuntime:
 def __init__(self):self.strategies=StrategyRuntime();self.cycles=0;self.errors={};self.last_cycle=0
 def update(self,name,rows,ts):self.strategies.update(name,rows);self.cycles+=1;self.last_cycle=ts
 def fail(self,name):self.errors[name]=self.errors.get(name,0)+1
 def snapshot(self):return {"cycles":self.cycles,"last_cycle":self.last_cycle,"counts":self.strategies.counts(),"errors":dict(self.errors)}
