from app.correlation_guard import check
def test_same_base_concentration_blocks():
 assert not check(["BTC"],"BTC").allowed
 assert check(["ETH"],"BTC").allowed
