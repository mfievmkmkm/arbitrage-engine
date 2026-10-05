from dataclasses import dataclass
@dataclass(frozen=True)
class CrashDecision:
 safe:bool;action:str;details:dict
def decide(snapshot,known_trade_symbols=()):
 known=set(known_trade_symbols)
 if not snapshot:return CrashDecision(False,"NO_PRIVATE_STATE",{})
 unexpected=[];orders=[]
 for venue,data in snapshot.items():
  if not data["health"].ok:return CrashDecision(False,"PRIVATE_API_UNAVAILABLE",{"venue":venue})
  for o in data["orders"]:orders.append((venue,o.symbol,o.order_id))
  for p in data["positions"]:
   if p.symbol not in known:unexpected.append((venue,p.symbol,p.side,p.qty))
 if orders:return CrashDecision(False,"CANCEL_OR_REVIEW_ORDERS",{"orders":orders})
 if unexpected:return CrashDecision(False,"UNEXPECTED_EXPOSURE",{"positions":unexpected})
 return CrashDecision(True,"RESUME_OBSERVATION",{})
