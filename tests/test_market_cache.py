from app.market_cache import MarketCache
def test_stale_book_rejected():
    c=MarketCache(5);c.put("a","X",{"bids":[]},ts=10)
    assert c.get("a","X",now=14) is not None
    assert c.get("a","X",now=16) is None
