from enum import Enum
class State(str,Enum):
 SCANNING="SCANNING";VALIDATING="VALIDATING";READY="READY";ENTERING="ENTERING";HEDGED="HEDGED";EXITING="EXITING";RECOVERY="RECOVERY";CLOSED="CLOSED";HALTED="HALTED"
NEXT={
 State.SCANNING:{State.VALIDATING,State.HALTED},
 State.VALIDATING:{State.READY,State.SCANNING,State.HALTED},
 State.READY:{State.ENTERING,State.SCANNING,State.HALTED},
 State.ENTERING:{State.HEDGED,State.RECOVERY,State.HALTED},
 State.HEDGED:{State.EXITING,State.RECOVERY,State.HALTED},
 State.EXITING:{State.CLOSED,State.RECOVERY,State.HALTED},
 State.RECOVERY:{State.HEDGED,State.CLOSED,State.HALTED},
 State.CLOSED:{State.SCANNING},
 State.HALTED:{State.SCANNING}}
class Machine:
 def __init__(self):self.state=State.SCANNING
 def move(self,target):
  target=State(target)
  if target not in NEXT[self.state]:raise ValueError("invalid transition")
  self.state=target;return target
