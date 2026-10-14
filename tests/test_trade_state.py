import pytest
from app.trade_state import Machine,State
def test_lifecycle():
 m=Machine()
 for s in (State.VALIDATING,State.READY,State.ENTERING,State.HEDGED,State.EXITING,State.CLOSED):m.move(s)
 assert m.state==State.CLOSED
 with pytest.raises(ValueError):m.move(State.HEDGED)
