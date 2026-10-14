from dataclasses import dataclass
@dataclass
class ErrorCircuit:
 threshold:int=3
 count:int=0
 tripped:bool=False
 reason:str=""
 def success(self):self.count=0
 def failure(self,reason):
  self.count+=1;self.reason=reason
  if self.count>=self.threshold:self.tripped=True
  return self.tripped
 def reset(self):self.count=0;self.tripped=False;self.reason=""
