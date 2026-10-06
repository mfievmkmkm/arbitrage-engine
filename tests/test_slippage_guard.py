from app.slippage_guard import check
def test_slippage_is_side_aware():
 assert not check(100,101,"buy",.5).allowed
 assert not check(100,99,"sell",.5).allowed
 assert check(100,99,"buy",.5).allowed
