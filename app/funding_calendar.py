from dataclasses import dataclass
@dataclass(frozen=True)
class Settlement:
 venue:str;symbol:str;rate:float;next_ts:float;interval_hours:float

def upcoming(rows,now,horizon=3600):return sorted([x for x in rows if now<=x.next_ts<=now+horizon],key=lambda x:x.next_ts)
