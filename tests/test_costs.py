from app.costs import taker_round_trip,net_edge_usd
def test_round_trip_cost():
    c=taker_round_trip(10,5,5)
    assert round(c.total_usd,4)==.02
    assert round(net_edge_usd(.10,c),4)==.08
