from app.dynamic_exit import ExitState,decide
def test_target_and_trailing_and_time():
 assert decide(ExitState(10,0),1,7).reason=="TARGET_CAPTURE"
 s=ExitState(10,0);assert not decide(s,1,5).close
 assert decide(s,2,3).reason=="NET_TRAILING"
 assert decide(ExitState(10,0),1200,0).reason=="TIME_STOP"
