from app.live_guard import LiveGuard
from app.live_arm import arm_from_snapshot,arm_after_streams
class H:
 ok=True
def test_requires_private_streams_after_reconcile():
 g=LiveGuard();d=arm_from_snapshot(g,{"x":{"health":H(),"positions":[],"orders":[]}})
 assert not d.armed and g.position_state_trusted and not g.can_trade()
 assert arm_after_streams(g).armed and g.can_trade()
