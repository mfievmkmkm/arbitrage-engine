from app.entry_gate import check
def test_gate():
 assert check(2,1,True,True,True).allowed
 assert check(2,1,True,True,False).reason=="FUNDING_UNKNOWN"
 assert check(2,1,True,True,True,True,False).reason=="PRIVATE_STATE_NOT_READY"
