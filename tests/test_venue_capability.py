from app.venue_capability import evaluate
def test_live_venue_requires_all_capabilities():
 assert evaluate(True,True,True).live
 assert not evaluate(False,True,True).live
