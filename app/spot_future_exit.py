from dataclasses import dataclass
@dataclass(frozen=True)
class Exit:
 close:bool;reason:str

def decide(net,best,age,max_age,trailing=.2):
 if age>=max_age:return Exit(True,"TIME_STOP")
 if best>0 and net<=best*(1-trailing):return Exit(True,"NET_TRAILING")
 return Exit(False,"HOLD")
