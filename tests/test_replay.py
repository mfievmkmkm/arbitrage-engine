from app.replay import ReplayParams,simulate
def test_target_exit():
 marks=[(10,.01,8),(20,.03,5),(30,.05,2)]
 r=simulate(10,marks,ReplayParams(.7,.2,100))
 assert r["reason"]=="TARGET" and r["net"]==.05
def test_trailing_exit():
 marks=[(10,.05,8),(20,.10,7),(30,.07,7)]
 r=simulate(10,marks,ReplayParams(.9,.2,100))
 assert r["reason"]=="TRAILING"
