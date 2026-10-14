from dataclasses import dataclass
@dataclass(frozen=True)
class NetEdge:
 executable_pct:float
 fee_pct:float
 funding_pct:float=0.0
 safety_pct:float=0.0
 @property
 def net_pct(self):return self.executable_pct-self.fee_pct+self.funding_pct-self.safety_pct
def calculate(executable_pct,fee_pct,funding_pct=0.0,safety_pct=0.0):
 return NetEdge(executable_pct,fee_pct,funding_pct,safety_pct)
