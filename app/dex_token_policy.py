from dataclasses import dataclass
@dataclass(frozen=True)
class TokenPolicy:
 allowed:bool;reasons:tuple

def check(contract_match,decimals_known,buy_tax,sell_tax,max_tax_pct=1):
 reasons=[]
 if not contract_match:reasons.append("CONTRACT_MISMATCH")
 if not decimals_known:reasons.append("DECIMALS_UNKNOWN")
 if buy_tax is None or sell_tax is None:reasons.append("TAX_UNKNOWN")
 elif max(buy_tax,sell_tax)>max_tax_pct:reasons.append("TOKEN_TAX_HIGH")
 return TokenPolicy(not reasons,tuple(reasons))
