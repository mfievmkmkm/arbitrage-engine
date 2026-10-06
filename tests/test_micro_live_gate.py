from app.micro_live_gate import check
class R:micro_live=False
def test_locked():
 x=check(False,R(),True,True,True,1)
 assert not x.allowed and "LIVE_DISABLED" in x.reasons
