from dataclasses import dataclass
@dataclass(frozen=True)
class KillState:
 blocked:bool
 scope:str
 reason:str
class KillSwitch:
 def __init__(self):self.global_reason="";self.venues={};self.pairs={}
 def trip_global(self,reason):self.global_reason=reason
 def trip_venue(self,venue,reason):self.venues[venue]=reason
 def trip_pair(self,symbol,reason):self.pairs[symbol]=reason
 def clear_global(self):self.global_reason=""
 def check(self,symbol,long_venue,short_venue):
  if self.global_reason:return KillState(True,"GLOBAL",self.global_reason)
  for v in (long_venue,short_venue):
   if v in self.venues:return KillState(True,"VENUE:"+v,self.venues[v])
  if symbol in self.pairs:return KillState(True,"PAIR:"+symbol,self.pairs[symbol])
  return KillState(False,"","OK")
