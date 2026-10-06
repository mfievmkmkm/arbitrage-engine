from app.runtime_private_reconcile import verify_trade
from app.runtime_state import RuntimeTrade
from app.private_adapter import PrivatePosition
class H:ok=True
def test_restart_detects_persisted_private_size_mismatch():
 t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0)
 s={"a":{"health":H(),"positions":[PrivatePosition("a","X","long",.8)]},"b":{"health":H(),"positions":[PrivatePosition("b","X","short",1)]}}
 x=verify_trade(t,s);assert not x.safe and x.reason=="PERSISTED_PRIVATE_MISMATCH"
