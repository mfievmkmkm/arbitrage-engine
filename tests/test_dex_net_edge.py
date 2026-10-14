from app.dex_net_edge import calculate
def test_dex_net_includes_all_drag():
 x=calculate(5,1,1,1,1,.5);assert x.net_usd==.5 and x.allowed
