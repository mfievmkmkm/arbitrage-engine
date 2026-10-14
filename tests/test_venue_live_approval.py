from app.venue_live_approval import approve
from types import SimpleNamespace
def test_probe_alone_does_not_approve_live_orders():
 p=SimpleNamespace(private_positions=True);assert not approve(p).approved
 assert approve(p,True,True).approved
