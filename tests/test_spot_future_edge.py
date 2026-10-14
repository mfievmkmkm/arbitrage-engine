from app.spot_future_edge import calculate
def test_spot_future_basis_direction():
 x=calculate(100,99.9,105,104.9,.2);assert x.direction=="LONG_SPOT_SHORT_FUTURE" and x.net_pct>4
