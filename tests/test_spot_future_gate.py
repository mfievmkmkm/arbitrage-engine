from app.spot_future_gate import check
def test_spot_short_requires_borrow():
 assert check("LONG_SPOT_SHORT_FUTURE",10,5).allowed
 assert not check("LONG_FUTURE_SHORT_SPOT",10,5,False).allowed
