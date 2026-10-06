from app.strategy_allocator import allocate
def test_only_futures_futures_can_receive_live_budget_now():
 assert allocate("futures_futures",5,50,True).allowed
 assert not allocate("spot_futures",2,50,True).allowed
 assert allocate("spot_futures",2,50,False).allowed
