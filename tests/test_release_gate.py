from app.release_gate import evaluate
def test_paper_ready_live_locked():
 x=evaluate(True,True,True,True)
 assert x.discovery and x.paper and not x.micro_live
def test_live_requires_evidence():
 x=evaluate(True,True,True,True,100,True,True,True)
 assert x.micro_live
