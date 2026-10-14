from app.net_edge import calculate
def test_net():
 assert round(calculate(2.5,.2,.03,.1).net_pct,2)==2.23
