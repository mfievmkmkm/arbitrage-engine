from app.private_state import PrivateState
def test_private_state_gate():
    s=PrivateState(connected=True);assert not s.ready
    s.reconciled();assert s.ready
    s.invalidate("STREAM_LOST");assert not s.ready
