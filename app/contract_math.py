from dataclasses import dataclass
@dataclass(frozen=True)
class ContractExposure:
    contracts: float
    contract_size: float
    base_amount: float
def normalize(contracts, contract_size):
    size=float(contract_size or 1.0)
    return ContractExposure(float(contracts),size,float(contracts)*size)
def difference(a,b):
    return abs(a.base_amount-b.base_amount)
def balanced(a,b,tolerance_pct=0.1):
    scale=max(abs(a.base_amount),abs(b.base_amount))
    return scale>0 and difference(a,b)<=scale*tolerance_pct/100
