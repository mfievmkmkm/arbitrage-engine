from dataclasses import dataclass
@dataclass(frozen=True)
class RouteValidation:
 allowed:bool;reasons:tuple

def check(quote,expected_chain,max_impact_pct,max_gas_usd):
 reasons=[]
 if not quote.valid:reasons.append("QUOTE_INVALID")
 if quote.chain!=expected_chain:reasons.append("CHAIN_MISMATCH")
 if quote.price_impact_pct>max_impact_pct:reasons.append("PRICE_IMPACT")
 if quote.gas_usd>max_gas_usd:reasons.append("GAS_LIMIT")
 return RouteValidation(not reasons,tuple(reasons))
