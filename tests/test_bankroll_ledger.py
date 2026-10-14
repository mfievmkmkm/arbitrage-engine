from app.bankroll_ledger import Ledger
def test_equity_attributes_costs_and_funding():
 x=Ledger(50);x.apply(2,.5,.2,.1);assert abs(x.equity-51.6)<1e-9
