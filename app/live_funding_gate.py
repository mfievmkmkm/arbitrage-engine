from dataclasses import dataclass
@dataclass(frozen=True)
class FundingGate:
 allowed:bool
 reason:str
 expected_usd:float|None
def check(funding_known,expected_usd,max_cost_usd):
 if not funding_known or expected_usd is None:return FundingGate(False,"FUNDING_UNKNOWN",None)
 if expected_usd < -abs(max_cost_usd):return FundingGate(False,"FUNDING_COST_TOO_HIGH",expected_usd)
 return FundingGate(True,"OK",expected_usd)
