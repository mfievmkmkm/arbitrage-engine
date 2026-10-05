from dataclasses import dataclass
@dataclass(frozen=True)
class PlannedLeg:
 venue:str;symbol:str;side:str;contracts:float;contract_size:float;base_amount:float
@dataclass(frozen=True)
class ExecutionPlan:
 long:PlannedLeg;short:PlannedLeg;base_amount:float;valid:bool;reason:str
def build(symbol,long_venue,short_venue,base_amount,long_contract_size,short_contract_size,long_round,short_round):
 if base_amount<=0:return ExecutionPlan(None,None,0,False,"INVALID_SIZE")
 lc=long_round(base_amount/long_contract_size);sc=short_round(base_amount/short_contract_size)
 lb=lc*long_contract_size;sb=sc*short_contract_size
 matched=min(lb,sb);scale=max(lb,sb)
 if matched<=0:return ExecutionPlan(None,None,0,False,"BELOW_MIN_SIZE")
 if abs(lb-sb)>scale*.001:return ExecutionPlan(None,None,matched,False,"EXPOSURE_MISMATCH")
 return ExecutionPlan(PlannedLeg(long_venue,symbol,"buy",lc,long_contract_size,lb),PlannedLeg(short_venue,symbol,"sell",sc,short_contract_size,sb),matched,True,"OK")
