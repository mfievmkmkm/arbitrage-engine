from dataclasses import dataclass
@dataclass(frozen=True)
class Canary:
 allowed:bool;reason:str

def check(verified_live_trades,incidents_since,last_net,max_trades=5):
 if verified_live_trades>=max_trades:return Canary(False,"CANARY_COMPLETE")
 if incidents_since>0:return Canary(False,"CANARY_INCIDENT")
 if last_net is not None and last_net<0:return Canary(False,"CANARY_LOSS_REVIEW")
 return Canary(True,"CANARY_ALLOWED")
