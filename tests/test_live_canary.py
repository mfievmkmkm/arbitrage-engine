from app.live_canary import check
def test_canary_stops_on_first_incident_or_loss():
 assert check(0,0,None).allowed
 assert not check(1,1,1).allowed
 assert not check(1,0,-.1).allowed
