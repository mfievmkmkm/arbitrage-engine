from dataclasses import dataclass
@dataclass(frozen=True)
class ResumeDecision:
 allowed:bool
 reason:str
def evaluate(operator_stopped,integrity_safe,kill_clear,private_ready,unknown_orders):
 if operator_stopped:return ResumeDecision(False,"OPERATOR_STOPPED")
 if not integrity_safe:return ResumeDecision(False,"INTEGRITY_UNSAFE")
 if not kill_clear:return ResumeDecision(False,"KILL_SWITCH_ACTIVE")
 if not private_ready:return ResumeDecision(False,"PRIVATE_NOT_READY")
 if unknown_orders:return ResumeDecision(False,"UNKNOWN_ORDERS")
 return ResumeDecision(True,"RESUME_ALLOWED")
