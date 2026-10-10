from app.portfolio_limits import check
def test_limits():
 assert check(50,0,10,0).allowed
 assert check(50,20,10,1).reason=="MAX_UTILIZATION"
 assert check(50,0,5,2).reason=="MAX_TRADES"
