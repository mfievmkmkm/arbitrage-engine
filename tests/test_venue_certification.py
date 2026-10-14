from app.venue_certification import REQUIRED,evaluate
def test_live_venue_needs_all_operational_evidence():
 assert evaluate({x:True for x in REQUIRED})[0]
 assert not evaluate({})[0]
