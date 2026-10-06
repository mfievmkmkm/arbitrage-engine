from dataclasses import dataclass
@dataclass(frozen=True)
class LiveMetrics:
 entries:int=0
 closes:int=0
 recoveries:int=0
 incidents:int=0
 unknown_orders:int=0
 realized_net:float=0
 def row(self):return self.__dict__
