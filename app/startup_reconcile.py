from dataclasses import dataclass
from .startup_guard import evaluate_startup
@dataclass(frozen=True)
class VenueStartup:
 venue:str
 safe:bool
 action:str
def evaluate_snapshot(snapshot):
 rows=[]
 for venue,data in snapshot.items():
  h=data["health"]
  if not h.ok:
   rows.append(VenueStartup(venue,False,"PRIVATE_API_UNAVAILABLE"));continue
  d=evaluate_startup(data["positions"],data["orders"])
  rows.append(VenueStartup(venue,d.safe,d.action))
 return rows
def all_ready(rows):
 return bool(rows) and all(x.safe for x in rows)
