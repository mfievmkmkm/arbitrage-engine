from dataclasses import dataclass,field
@dataclass
class Metrics:
 cycles:int=0;errors:int=0;observations:int=0;last_latency_ms:float=0
 def success(self,count,latency):self.cycles+=1;self.observations+=count;self.last_latency_ms=latency
 def fail(self):self.cycles+=1;self.errors+=1
 @property
 def success_pct(self):return 100*(self.cycles-self.errors)/self.cycles if self.cycles else 0
