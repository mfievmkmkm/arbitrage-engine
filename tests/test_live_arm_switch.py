from app.live_arm import arm_after_streams
from app.live_guard import LiveGuard
def test_live_switch_required():
 g=LiveGuard(position_state_trusted=True)
 x=arm_after_streams(g,False)
 assert not x.armed and x.reason=="LIVE_DISABLED" and not g.enabled
