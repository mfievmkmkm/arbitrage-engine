from app.micro_live_evidence import evaluate
def test_micro_live_evidence_fail_closed():
 x=evaluate(True,True,True,True,True,True,True,False,True);assert x.ready
 assert "FEE_UNVERIFIED" in evaluate(True,True,True,False,True,True,True,False,True).reasons
