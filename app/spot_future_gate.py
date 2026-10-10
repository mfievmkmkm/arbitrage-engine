from dataclasses import dataclass
@dataclass(frozen=True)
class SpotFutureGate:
 allowed:bool;reason:str

def check(direction,spot_balance,base_required,borrow_available=False):
 if direction=="LONG_SPOT_SHORT_FUTURE":return SpotFutureGate(spot_balance>=base_required,"OK" if spot_balance>=base_required else "SPOT_QUOTE_BALANCE_LOW")
 if not borrow_available:return SpotFutureGate(False,"SPOT_SHORT_BORROW_UNAVAILABLE")
 return SpotFutureGate(True,"OK")
