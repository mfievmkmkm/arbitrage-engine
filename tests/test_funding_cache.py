from app.funding_cache import FundingCache
def test_ttl():
    c=FundingCache(60);c.put("a","X",.001,now=100)
    assert c.get("a","X",now=150)["rate"]==.001
    assert c.get("a","X",now=161) is None
