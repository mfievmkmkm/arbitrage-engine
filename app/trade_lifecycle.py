from dataclasses import dataclass,field
from enum import Enum
class Phase(str,Enum):
 PLANNED="PLANNED";ENTERING="ENTERING";RECOVERY="RECOVERY";HEDGED="HEDGED";EXITING="EXITING";CLOSED="CLOSED";FAILED="FAILED"
_ALLOWED={Phase.PLANNED:{Phase.ENTERING,Phase.FAILED},Phase.ENTERING:{Phase.HEDGED,Phase.RECOVERY,Phase.FAILED},Phase.RECOVERY:{Phase.HEDGED,Phase.CLOSED,Phase.FAILED},Phase.HEDGED:{Phase.EXITING,Phase.RECOVERY},Phase.EXITING:{Phase.CLOSED,Phase.RECOVERY,Phase.FAILED},Phase.CLOSED:set(),Phase.FAILED:set()}
@dataclass
class Lifecycle:
 phase:Phase=Phase.PLANNED
 history:list=field(default_factory=lambda:[Phase.PLANNED])
 def move(self,nxt):
  if nxt not in _ALLOWED[self.phase]:raise ValueError(f"{self.phase}->{nxt}")
  self.phase=nxt;self.history.append(nxt)
