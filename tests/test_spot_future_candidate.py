from app.spot_future_candidate import check
def test_candidate_requires_fees_funding_and_fresh_books():
 op={"hypothetical_edge":2};assert check(op,1,True,True,True).allowed
 assert not check(op,1,True,False,True).allowed
