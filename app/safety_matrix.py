from dataclasses import dataclass
@dataclass(frozen=True)
class Safety:
 observation:bool;paper:bool;live:bool;reason:str
def evaluate(market_ok,db_ok,risk_ok,private_ok,state_trusted,streams_ready,live_enabled):
 if not market_ok:return Safety(False,False,False,"MARKET_DATA_DOWN")
 if not db_ok:return Safety(False,False,False,"DB_DOWN")
 if not risk_ok:return Safety(True,False,False,"RISK_HALTED")
 if not private_ok:return Safety(True,True,False,"PRIVATE_API_UNAVAILABLE")
 if not state_trusted:return Safety(True,True,False,"POSITION_STATE_UNTRUSTED")
 if not streams_ready:return Safety(True,True,False,"PRIVATE_STREAMS_NOT_READY")
 if not live_enabled:return Safety(True,True,False,"LIVE_DISABLED")
 return Safety(True,True,True,"OK")
