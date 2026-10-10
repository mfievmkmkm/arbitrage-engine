from app.live_market_evidence import validate
def test_market_evidence_combines_freshness_funding_slippage():
 x=validate(10,9.9,9.9,True,0,.1,100,110,100.1,109.9,.2);assert x.allowed
 y=validate(10,1,9.9,False,None,.1,100,110,None,None,.2);assert not y.allowed and "MARKET_DATA_STALE" in y.reasons and "FUNDING_UNKNOWN" in y.reasons
