from app.leverage_gate import check
def test_initial_micro_live_is_one_x():
 assert check(1,1).allowed
 assert not check(2,1).allowed
