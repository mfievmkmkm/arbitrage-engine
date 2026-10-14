from app.state_invariants import check
def test_hedged_and_closed_invariants():
 assert not check("HEDGED",False,True,False,False).safe
 assert check("HEDGED",True,True,False,False).safe
 assert not check("CLOSED_VERIFIED",True,False,True,False).safe
