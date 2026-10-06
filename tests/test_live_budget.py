from app.live_budget import allocate
def test_50_bankroll_is_capped_at_five_dollars():
 assert allocate(50,5).allowed
 assert not allocate(50,6).allowed and allocate(50,6).notional==5
