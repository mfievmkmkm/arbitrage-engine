from app.price_band_gate import check
def test_anomalous_price_blocks():
 assert check(100,100.5,1).safe
 assert not check(100,105,1).safe
