from app.opportunity_gate import check
def test_gate():
 o={"hypothetical_edge":2,"funding_known":True,"notional":5}
 assert check(o,1,True,True,False,False,50,0,0).allowed
 assert not check(o,3,True,True,False,False,50,0,0).allowed
