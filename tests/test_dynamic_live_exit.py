from app.dynamic_live_exit import decide
def test_dynamic_exit_trailing_time_and_opportunity():
 assert decide(5,10,.2,0,1,100).reason=="NET_TRAILING"
 assert decide(0,0,.2,0,101,100).reason=="TIME_STOP"
 assert decide(0,0,.2,0,1,100,1,3,.2,1).reason=="BETTER_OPPORTUNITY"
 assert not decide(0,0,.2,0,1,100).exit
