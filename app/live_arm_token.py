from dataclasses import dataclass
@dataclass(frozen=True)
class ArmToken:
 valid:bool
 reason:str
def issue(live_enabled,acceptance_passed,resume_safe,operator_stopped):
 if not live_enabled:return ArmToken(False,"LIVE_DISABLED")
 if not acceptance_passed:return ArmToken(False,"ACCEPTANCE_NOT_PASSED")
 if not resume_safe:return ArmToken(False,"RESUME_EVIDENCE_UNSAFE")
 if operator_stopped:return ArmToken(False,"OPERATOR_STOPPED")
 return ArmToken(True,"ARMED")
