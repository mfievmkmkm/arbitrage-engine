from app.funding import FundingSnapshot,carry_pct
def test_funding_direction():
    a=FundingSnapshot("a","X",.0001,None,8);b=FundingSnapshot("b","X",.0003,None,8)
    assert round(carry_pct(a,b),4)==.02
