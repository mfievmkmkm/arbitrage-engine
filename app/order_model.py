from dataclasses import dataclass,field
from enum import Enum
class OrderState(str,Enum):
 NEW="NEW";OPEN="OPEN";PARTIAL="PARTIAL";FILLED="FILLED";CANCELED="CANCELED";REJECTED="REJECTED"
@dataclass
class Fill:
 qty:float;price:float;fee:float=0.0
@dataclass
class Order:
 venue:str;symbol:str;side:str;qty:float;state:OrderState=OrderState.NEW;fills:list=field(default_factory=list)
 @property
 def filled(self):return sum(x.qty for x in self.fills)
 @property
 def remaining(self):return max(0.0,self.qty-self.filled)
 @property
 def avg_price(self):
  q=self.filled
  return None if q<=0 else sum(x.qty*x.price for x in self.fills)/q
 def add_fill(self,fill):
  self.fills.append(fill)
  self.state=OrderState.FILLED if self.remaining<=1e-12 else OrderState.PARTIAL
