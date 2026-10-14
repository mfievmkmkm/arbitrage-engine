from app.spot_spot_directional import best
def test_reverse_direction_uses_vwap_too():
 a={"asks":[[105,10]],"bids":[[104,10]]};b={"asks":[[100,10]],"bids":[[99,10]]};x=best("a",a,"b",b,10,.1,.1);assert x["buy_venue"]=="b" and x["sell_venue"]=="a" and x["net"]>3
