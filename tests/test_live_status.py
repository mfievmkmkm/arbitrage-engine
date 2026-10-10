from app.live_status import build
from app.live_supervisor import LiveSupervisor
def test_live_status_lists_lock_reasons():
 s=LiveSupervisor();x=build(s,0,True,False,False)
 assert not x.can_enter and "WITHDRAW_PERMISSION" in x.reasons and "VENUE_CAPABILITY" in x.reasons
