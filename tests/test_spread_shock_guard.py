from app.spread_shock_guard import check
def test_execution_spread_shock_blocks():
 assert check(10,10.01,.25).safe
 assert not check(10,10.1,.25).safe
