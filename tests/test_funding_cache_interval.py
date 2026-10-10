from app.funding_cache import FundingCache
def test_cache_preserves_interval():
 x=FundingCache(10);x.put("a","X",.1,100,8,now=1);assert x.get("a","X",now=2)["interval_hours"]==8
