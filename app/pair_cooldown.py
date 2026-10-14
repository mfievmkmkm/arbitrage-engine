from dataclasses import dataclass
@dataclass(frozen=True)
class PairCooldown:
 allowed:bool
 remaining:float
 reason:str
def check(now,last_incident,cooldown=60):
 if last_incident is None:return PairCooldown(True,0,"OK")
 left=max(0,cooldown-(now-last_incident))
 return PairCooldown(left<=0,left,"OK" if left<=0 else "PAIR_COOLDOWN")
