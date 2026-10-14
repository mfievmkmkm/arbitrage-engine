from app.live_funding_gate import check
def test_funding_unknown_and_expensive_block():
 assert not check(False,None,.1).allowed
 assert not check(True,-.2,.1).allowed
 assert check(True,.05,.1).allowed
