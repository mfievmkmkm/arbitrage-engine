from app.live_commands import resume
from app.operator_stop import StopController
from app.kill_switch import KillSwitch
from types import SimpleNamespace
def test_resume_requires_reconciliation_evidence():
 s=StopController(True,"STOP");k=KillSwitch();assert not resume(s,SimpleNamespace(safe=False,reasons=("UNKNOWN_ORDERS",)),k).allowed
 assert resume(s,SimpleNamespace(safe=True,reasons=()),k).allowed and not s.stopped
