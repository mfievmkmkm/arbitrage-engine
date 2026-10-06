from app.position_manager import PositionManager
from app.runtime_state import RuntimeTrade

def test_mark_net_includes_estimated_exit_fees_and_funding():
 t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0,entry_fees=1,funding=-.5)
 m=PositionManager(long_exit_fee_rate=.01,short_exit_fee_rate=.01,safety_buffer=.25)
 x=m.mark(t,104,106,now=1)
 assert abs(x.gross-8)<1e-12
 assert abs(x.fees-3.35)<1e-12
 assert abs(x.net-4.15)<1e-12

def test_expensive_exit_does_not_trigger_false_profit_target():
 t=RuntimeTrade("t","X","a","b",1,1,1,1,1,100,110,0)
 x=PositionManager(target_capture=.7,long_exit_fee_rate=.03,short_exit_fee_rate=.03).mark(t,104,106,now=1)
 assert x.net<7 and not x.decision.close
