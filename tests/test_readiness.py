from app.readiness import assess
def test_paper_does_not_require_private():
 assert assess(True,True,True,True,True).ready
 assert not assess(True,True,True,True,True,False,True).ready
