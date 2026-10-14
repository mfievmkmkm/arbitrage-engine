from app.funding_ledger import FundingLedger
def test_funding_settlements_sum():
 x=FundingLedger();x.add("t","a",.1,1);x.add("t","b",-.02,1);assert abs(x.total()-.08)<1e-9
