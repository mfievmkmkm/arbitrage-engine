from dataclasses import dataclass
@dataclass(frozen=True)
class GasBudget:
 allowed:bool;reason:str

def check(gas_usd,net_before_gas,max_share=.25):
 if gas_usd<0 or net_before_gas<=0:return GasBudget(False,"GAS_OR_EDGE_INVALID")
 return GasBudget(gas_usd<=net_before_gas*max_share,"OK" if gas_usd<=net_before_gas*max_share else "GAS_TOO_LARGE")
