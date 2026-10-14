from dataclasses import dataclass
@dataclass(frozen=True)
class Promotion:
 allowed:bool;reason:str

def check(ai_recommended,replay_validated,operator_approved):
 if not ai_recommended:return Promotion(False,"NO_RECOMMENDATION")
 if not replay_validated:return Promotion(False,"REPLAY_NOT_VALIDATED")
 if not operator_approved:return Promotion(False,"OPERATOR_APPROVAL_REQUIRED")
 return Promotion(True,"PROMOTE")
