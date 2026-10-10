from dataclasses import dataclass
@dataclass(frozen=True)
class Probe:
 client_id:bool
 reduce_only:bool
 private_positions:bool
 notes:tuple
def from_exchange(exchange):
 has=getattr(exchange,"has",{}) or {}
 client=bool(has.get("createOrder"))
 reduce=bool(has.get("createReduceOnlyOrder") or has.get("createOrder"))
 private=bool(has.get("fetchPositions"))
 notes=[]
 if not has.get("createOrder"):notes.append("CREATE_ORDER_UNAVAILABLE")
 if not has.get("fetchPositions"):notes.append("FETCH_POSITIONS_UNAVAILABLE")
 # reduceOnly and clientOrderId still require venue-specific dry-run/config evidence before LIVE.
 return Probe(client,reduce,private,tuple(notes))
