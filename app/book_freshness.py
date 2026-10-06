from dataclasses import dataclass
@dataclass(frozen=True)
class Freshness:
 ok:bool
 reason:str
 age_ms:float
def check(now_ts,book_ts,max_age_ms=1500):
 age=max(0,(now_ts-book_ts)*1000)
 return Freshness(age<=max_age_ms,"OK" if age<=max_age_ms else "MARKET_DATA_STALE",age)
