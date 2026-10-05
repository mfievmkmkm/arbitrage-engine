import time
from dataclasses import dataclass
@dataclass(frozen=True)
class FundingWindow:
 due:bool;periods:int;reason:str
def window(next_ts,hold_seconds,interval_hours=None,now_ms=None):
 if next_ts is None:return FundingWindow(False,0,"UNKNOWN")
 now_ms=int(time.time()*1000) if now_ms is None else now_ms
 ts=int(next_ts)
 if ts<10_000_000_000:ts*=1000
 end=now_ms+int(hold_seconds*1000)
 if ts>end:return FundingWindow(False,0,"NOT_DUE")
 if interval_hours is None:return FundingWindow(True,1,"DUE")
 interval=int(interval_hours*3600*1000)
 return FundingWindow(True,1+max(0,(end-ts)//interval),"DUE")
def carry_pct(long_rate,short_rate,w):
 if not w.due:return 0.0
 return (short_rate-long_rate)*100*w.periods
