from dataclasses import dataclass
from enum import Enum
class ExecState(str,Enum):
    READY="READY";PARTIAL="PARTIAL";HEDGED="HEDGED";CLOSED="CLOSED"
@dataclass
class Leg:
    venue:str;side:str;target_qty:float;filled_qty:float=0.0
@dataclass
class Hedge:
    long:Leg;short:Leg;state:ExecState=ExecState.READY
    @property
    def mismatch(self):return abs(self.long.filled_qty-self.short.filled_qty)
    @property
    def hedged(self):
        base=max(self.long.filled_qty,self.short.filled_qty)
        return base>0 and self.mismatch<=base*0.001
class HedgeController:
    def assess(self,h):
        if h.hedged:h.state=ExecState.HEDGED;return "HOLD"
        if h.long.filled_qty or h.short.filled_qty:h.state=ExecState.PARTIAL;return "RECOVER"
        return "WAIT"
    def recovery(self,complete_cost,flatten_cost,remaining_edge):
        if remaining_edge<=0:return "FLATTEN"
        return "COMPLETE" if complete_cost<flatten_cost else "FLATTEN"
