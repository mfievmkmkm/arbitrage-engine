from app.execution import Leg,Hedge,HedgeController,ExecState
def test_partial_recovery():
    h=Hedge(Leg("a","buy",5,5),Leg("b","sell",5,0));c=HedgeController()
    assert c.assess(h)=="RECOVER" and h.state==ExecState.PARTIAL
    assert c.recovery(.01,.03,.2)=="COMPLETE"
    assert c.recovery(.01,.03,-.1)=="FLATTEN"
