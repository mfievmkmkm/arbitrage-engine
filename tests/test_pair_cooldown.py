from app.pair_cooldown import check
def test_pair_cooldown_after_incident():
 assert not check(100,90,60).allowed
 assert check(200,90,60).allowed
