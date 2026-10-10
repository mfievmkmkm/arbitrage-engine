from dataclasses import dataclass
@dataclass(frozen=True)
class Capacity:
 allowed:bool
 reason:str
def check(open_trades,max_open=1):
 return Capacity(open_trades<max_open,"OK" if open_trades<max_open else "MAX_OPEN_TRADES")
