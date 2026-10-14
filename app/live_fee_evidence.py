from dataclasses import dataclass
@dataclass(frozen=True)
class FeeEvidence:
 rate:float|None
 source:str
 verified:bool
def live_rate(evidence):
 if evidence is None or evidence.rate is None or not evidence.verified:raise RuntimeError("LIVE_FEE_UNVERIFIED")
 if evidence.rate<0:raise RuntimeError("LIVE_FEE_INVALID")
 return evidence.rate
