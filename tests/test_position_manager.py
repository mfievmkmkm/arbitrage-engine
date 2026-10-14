from app.position_manager import PositionManager
from app.runtime_state import RuntimeTrade
def test_mark():
 t=RuntimeTrade("1","X","a","b",1,1,1,1,1,100,110,0)
 x=PositionManager(max_seconds=1).mark(t,100,110,now=2)
 assert x.decision.close
