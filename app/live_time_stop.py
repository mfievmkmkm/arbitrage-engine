from dataclasses import dataclass
@dataclass(frozen=True)
class TimeStop:
 exit:bool
 reason:str
def check(opened_at,now,max_seconds):
 return TimeStop(now-opened_at>=max_seconds,"TIME_STOP" if now-opened_at>=max_seconds else "HOLD")
