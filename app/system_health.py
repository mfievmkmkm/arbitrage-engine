from dataclasses import dataclass
@dataclass(frozen=True)
class SystemHealth:
 market_ok:bool;private_ok:bool;db_ok:bool;risk_ok:bool;live_ready:bool;status:str
def build(market_ok,private_ok,db_ok,risk_ok,live_ready):
 critical=market_ok and db_ok and risk_ok
 if not critical:return SystemHealth(market_ok,private_ok,db_ok,risk_ok,False,"HALTED")
 if not private_ok:return SystemHealth(market_ok,private_ok,db_ok,risk_ok,False,"OBSERVATION_ONLY")
 return SystemHealth(market_ok,private_ok,db_ok,risk_ok,live_ready,"LIVE_READY" if live_ready else "SAFE")
