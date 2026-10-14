from enum import Enum
class TradeState(str,Enum):
 SCANNING="SCANNING";OPPORTUNITY="OPPORTUNITY";VALIDATING="VALIDATING";READY="READY";ENTERING="ENTERING";HEDGED="HEDGED";EXITING="EXITING";CLOSED="CLOSED";RECOVERY="RECOVERY";HALTED="HALTED"
ALLOWED={
 TradeState.SCANNING:{TradeState.OPPORTUNITY,TradeState.HALTED},
 TradeState.OPPORTUNITY:{TradeState.VALIDATING,TradeState.SCANNING,TradeState.HALTED},
 TradeState.VALIDATING:{TradeState.READY,TradeState.SCANNING,TradeState.HALTED},
 TradeState.READY:{TradeState.ENTERING,TradeState.SCANNING,TradeState.HALTED},
 TradeState.ENTERING:{TradeState.HEDGED,TradeState.RECOVERY,TradeState.HALTED},
 TradeState.HEDGED:{TradeState.EXITING,TradeState.RECOVERY,TradeState.HALTED},
 TradeState.EXITING:{TradeState.CLOSED,TradeState.RECOVERY,TradeState.HALTED},
 TradeState.RECOVERY:{TradeState.HEDGED,TradeState.CLOSED,TradeState.HALTED},
 TradeState.CLOSED:{TradeState.SCANNING},
 TradeState.HALTED:{TradeState.SCANNING}}
class TradeMachine:
 def __init__(self):self.state=TradeState.SCANNING;self.history=[self.state]
 def move(self,target):
  target=TradeState(target)
  if target not in ALLOWED[self.state]:raise ValueError(f"invalid transition {self.state}->{target}")
  self.state=target;self.history.append(target);return target
