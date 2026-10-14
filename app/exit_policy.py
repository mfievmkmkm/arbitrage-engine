from dataclasses import dataclass
@dataclass(frozen=True)
class Decision:
 close:bool;reason:str

def decide(net,best,age,max_age,convergence,target,trailing,adverse=False,better=False):
 if adverse:return Decision(True,"ADVERSE_MARKET")
 if better:return Decision(True,"BETTER_OPPORTUNITY")
 if convergence>=target:return Decision(True,"TARGET_CONVERGENCE")
 if age>=max_age:return Decision(True,"TIME_STOP")
 if best>0 and net<=best*(1-trailing):return Decision(True,"NET_TRAILING")
 return Decision(False,"HOLD")
