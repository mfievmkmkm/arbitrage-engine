from app.margin_gate import check
def test_both_venues_need_margin_reserve():
 assert check(5,7,7).allowed
 assert not check(5,5,7).allowed
