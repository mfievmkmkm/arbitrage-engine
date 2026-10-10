from app.live_guard import LiveGuard
def test_live_requires_trusted_private_state():
    g=LiveGuard();assert not g.arm()
    g.private_streams_ready=True;g.position_state_trusted=True
    assert g.arm() and g.can_trade()
    g.kill("STATE_UNKNOWN");assert not g.can_trade()
