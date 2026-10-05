from dataclasses import dataclass
from .live_guard import LiveGuard
from .startup_reconcile import evaluate_snapshot,all_ready
@dataclass(frozen=True)
class ArmDecision:
 armed:bool
 reason:str
def arm_from_snapshot(guard:LiveGuard,snapshot):
 if not snapshot:
  guard.kill("NO_PRIVATE_VENUES");return ArmDecision(False,"NO_PRIVATE_VENUES")
 rows=evaluate_snapshot(snapshot)
 if not all_ready(rows):
  guard.kill("STARTUP_RECONCILIATION_FAILED");return ArmDecision(False,"STARTUP_RECONCILIATION_FAILED")
 guard.private_streams_ready=False
 guard.position_state_trusted=True
 guard.enabled=False
 guard.kill_reason="PRIVATE_STREAMS_NOT_READY"
 return ArmDecision(False,"PRIVATE_STREAMS_NOT_READY")
def arm_after_streams(guard:LiveGuard):
 guard.private_streams_ready=True
 if not guard.position_state_trusted:return ArmDecision(False,"POSITION_STATE_UNTRUSTED")
 return ArmDecision(guard.arm(),"ARMED" if guard.enabled else "ARM_FAILED")
