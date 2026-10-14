from app.live_capacity import check
def test_micro_live_one_trade_capacity():
 assert check(0).allowed and not check(1).allowed
