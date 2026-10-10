from dataclasses import dataclass
@dataclass(frozen=True)
class FundingWindow:
 safe:bool
 reason:str
 seconds_to_funding:float|None
def check(now,next_funding,expected_cost,max_cost,avoid_seconds=120):
 if next_funding is None:return FundingWindow(False,"NEXT_FUNDING_UNKNOWN",None)
 sec=next_funding-now
 if expected_cost is None:return FundingWindow(False,"FUNDING_COST_UNKNOWN",sec)
 if expected_cost< -abs(max_cost):return FundingWindow(False,"FUNDING_COST_TOO_HIGH",sec)
 if 0<=sec<=avoid_seconds and expected_cost<0:return FundingWindow(False,"ADVERSE_FUNDING_IMMINENT",sec)
 return FundingWindow(True,"OK",sec)
