from dataclasses import dataclass
@dataclass(frozen=True)
class Heartbeat:
 healthy:bool
 reason:str
def evaluate(scanner_age,private_age,db_ok,runtime_ok,max_scanner_age=5,max_private_age=5):
 if scanner_age>max_scanner_age:return Heartbeat(False,"SCANNER_HEARTBEAT_LOST")
 if private_age>max_private_age:return Heartbeat(False,"PRIVATE_HEARTBEAT_LOST")
 if not db_ok:return Heartbeat(False,"DB_UNHEALTHY")
 if not runtime_ok:return Heartbeat(False,"RUNTIME_UNHEALTHY")
 return Heartbeat(True,"OK")
