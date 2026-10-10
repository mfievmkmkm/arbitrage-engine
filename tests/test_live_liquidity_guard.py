from app.live_liquidity_guard import check
def test_both_legs_need_depth_reserve():
 assert check(1,1.2,1.2).safe
 assert not check(1,1.0,1.2).safe
