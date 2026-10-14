from app.spot_spot_edge import calculate
def test_spot_spot_net_edge():
 x=calculate("a","b",100,99.9,105,104.9,.2,.1);assert x.buy=="a" and x.sell=="b" and x.net_pct>4
