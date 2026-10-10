from app.clock_skew_gate import check
def test_large_exchange_clock_skew_blocks():
 assert check(1000,1500).safe
 assert not check(1000,3000).safe
