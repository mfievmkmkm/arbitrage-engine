from app.safety_matrix import evaluate
from app.invariants import closed
from app.funding_timing import window
from app.portfolio_limits import check
def test_live_never_arms_without_private_streams():
 assert not evaluate(True,True,True,True,True,False,True).live
def test_closed_means_zero_exposure():
 assert not closed(0,.0001).ok
def test_future_funding_not_charged():
 assert not window(2_000_000,1,8,1_000_000).due
def test_small_bank_cannot_overallocate():
 assert not check(50,20,10,0,max_utilization_pct=50).allowed
