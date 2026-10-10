import pytest
from app.trade_lifecycle import Lifecycle,Phase
def test_lifecycle():
 x=Lifecycle();x.move(Phase.ENTERING);x.move(Phase.HEDGED);x.move(Phase.EXITING);x.move(Phase.CLOSED)
 assert x.phase==Phase.CLOSED
 with pytest.raises(ValueError):x.move(Phase.ENTERING)
