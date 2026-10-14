from dataclasses import dataclass
@dataclass(frozen=True)
class VenueCapability:
 live:bool
 client_id:bool
 reduce_only:bool
 private_positions:bool
 reason:str
def evaluate(client_id,reduce_only,private_positions):
 ok=client_id and reduce_only and private_positions
 missing=[]
 if not client_id:missing.append("CLIENT_ID")
 if not reduce_only:missing.append("REDUCE_ONLY")
 if not private_positions:missing.append("PRIVATE_POSITIONS")
 return VenueCapability(ok,client_id,reduce_only,private_positions,"OK" if ok else "MISSING_"+",".join(missing))
