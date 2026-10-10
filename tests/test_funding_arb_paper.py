from app.funding_arb_paper import projected
def test_funding_paper_includes_fees_and_spread_risk():assert abs(projected(100,1,.2,.1)-.7)<1e-9
