from dataclasses import dataclass
@dataclass(frozen=True)
class VenueErrorState:
 blocked:bool
 count:int
 reason:str
class VenueErrors:
 def __init__(self,threshold=3):self.threshold=threshold;self.counts={}
 def success(self,venue):self.counts[venue]=0
 def failure(self,venue):
  n=self.counts.get(venue,0)+1;self.counts[venue]=n
  return VenueErrorState(n>=self.threshold,n,"VENUE_ERROR_LIMIT" if n>=self.threshold else "COUNTED")
