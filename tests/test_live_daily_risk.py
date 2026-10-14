from app.live_daily_risk import check
def test_daily_stop_at_two_percent():
 assert check(50,-.99).allowed
 assert not check(50,-1).allowed
