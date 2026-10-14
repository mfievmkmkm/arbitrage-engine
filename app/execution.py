from dataclasses import dataclass
from enum import Enum
from .contract_math import normalize,balanced
class ExecState(str,Enum):
    READY="READY";PARTIAL="PARTIAL";HEDGED="HEDGED";CLOSED="CLOSED"
@dataclass
class Leg:
    venue:str;side:str;target_qty:float;filled_qty:float=0.0;contract_size:float=1.0
    @property
    def exposure(self):return normalize(self.filled_qty,self.contract_size)
@dataclass
class Hedge:
    long:Leg;short:Leg;state:ExecState=ExecState.READY
    @property
    def mismatch(self):return abs(self.long.exposure.base_amount-self.short.exposure.base_amount)
    @property
    def hedged(self):return balanced(self.long.exposure,self.short.exposure,.1)
class HedgeController:
    def assess(self,h):
        if h.hedged:h.state=ExecState.HEDGED;return "HOLD"
        if h.long.filled_qty or h.short.filled_qty:h.state=ExecState.PARTIAL;return "RECOVER"
        return "WAIT"
    def recovery(self,complete_cost,flatten_cost,remaining_edge):
        if remaining_edge<=0:return "FLATTEN"
        return "COMPLETE" if complete_cost<flatten_cost else "FLATTEN"
