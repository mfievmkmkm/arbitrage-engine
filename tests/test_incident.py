from app.incident import classify
def test_unknown_state_has_priority():
 x=classify(False,False,True,True)
 assert x.code=="STATE_UNKNOWN" and x.action=="HALT_AND_RECONCILE"
