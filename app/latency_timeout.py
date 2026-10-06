from dataclasses import dataclass
@dataclass(frozen=True)
class TimeoutPolicy:
 submit:float
 private_verify:float
 recovery:float
def from_latency(latency_ms,min_submit=2,max_submit=10):
 base=max(min_submit,min(max_submit,float(latency_ms)/1000*6))
 return TimeoutPolicy(base,max(1,base/2),base)
