from dataclasses import dataclass
@dataclass(frozen=True)
class DexSafety:
 allowed:bool;reasons:tuple

def check(contract_verified,tax_known,gas_known,route_valid,min_received_known):
 reasons=[]
 for ok,r in ((contract_verified,"CONTRACT_UNVERIFIED"),(tax_known,"TOKEN_TAX_UNKNOWN"),(gas_known,"GAS_UNKNOWN"),(route_valid,"ROUTE_INVALID"),(min_received_known,"MIN_RECEIVED_UNKNOWN")):
  if not ok:reasons.append(r)
 return DexSafety(not reasons,tuple(reasons))
