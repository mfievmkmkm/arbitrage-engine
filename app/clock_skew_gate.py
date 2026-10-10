from dataclasses import dataclass
@dataclass(frozen=True)
class ClockSkew:
 safe:bool
 skew_ms:float
 reason:str
def check(local_ms,exchange_ms,max_skew_ms=1000):
 skew=abs(float(local_ms)-float(exchange_ms))
 return ClockSkew(skew<=max_skew_ms,skew,"OK" if skew<=max_skew_ms else "CLOCK_SKEW")
