import time
from dataclasses import dataclass
@dataclass(frozen=True)
class PhaseEvent:
 trade_id:str
 phase:str
 ts:float
 reason:str
async def persist(diary,trade_id,phase,reason=""):
 await diary.event(trade_id,"PHASE",reason=reason,payload={"phase":phase}) if hasattr(diary,"event") else None
 return PhaseEvent(trade_id,phase,time.time(),reason)
