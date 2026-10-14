from dataclasses import dataclass
@dataclass(frozen=True)
class VenueOperational:
 live:bool;reasons:tuple

def evaluate(markets,private,fees,clock,position_mode):
 reasons=[]
 for ok,r in ((markets,"MARKETS"),(private,"PRIVATE"),(fees,"FEES"),(clock,"CLOCK"),(position_mode,"POSITION_MODE")):
  if not ok:reasons.append(r)
 return VenueOperational(not reasons,tuple(reasons))
