from app.min_order_gate import check
def test_min_notional_both_venues():
 assert check(5,3,4).allowed
 assert not check(2,3,1).allowed
