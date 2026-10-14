from app.unknown_submit_policy import decide
def test_unknown_submit_never_blind_retries():
 assert decide("MISSING",False,0).action=="GLOBAL_HALT";assert decide("MISSING",True,0).action=="ABORT_NO_RETRY";assert "RETRY" not in decide("MISSING",True,1).action
