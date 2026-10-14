from dataclasses import dataclass
@dataclass(frozen=True)
class Campaign:
 ready:bool;reason:str

def status(closed_trades,replay_promoted,min_closed=100):
 if closed_trades<min_closed:return Campaign(False,f"PAPER_{closed_trades}/{min_closed}")
 if not replay_promoted:return Campaign(False,"REPLAY_NOT_PROMOTED")
 return Campaign(True,"READY_FOR_SEMI_AUTO_REVIEW")
